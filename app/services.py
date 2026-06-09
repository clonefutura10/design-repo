"""
Pipeline orchestration service.

Wraps src/ modules into a single callable for the API layer.
Mirrors the exact logic from run.py.
"""

from __future__ import annotations
import uuid
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import re

from src.pdf_parser.extractor import extract_crf
from src.pdf_parser.field_identifier import CRFField
from src.resolution.noise_filter import is_noise_field
from src.resolution.tier0_rules import Tier0Rules, _reset_usage_tracking, set_study_context
from src.resolution.tier1_not_submitted import Tier1NotSubmitted
from src.annotator.pdf_writer import annotate_pdf
from src.resolution.models import ResolutionResult, ResolutionTier
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineResult:
    """Stores a completed pipeline run."""
    job_id: str
    filename: str
    output_pdf_path: Path
    input_pdf_path: Path                              # kept for re-annotation on edits
    stats: dict[str, Any]
    all_results: list[ResolutionResult] = field(default_factory=list)   # aligned with data_fields
    data_fields: list[CRFField] = field(default_factory=list)           # extracted CRF fields
    resolved_results: list[ResolutionResult] = field(default_factory=list)
    unresolved_results: list[ResolutionResult] = field(default_factory=list)


# ── In-memory job store (swap for Redis/DB in production) ──
_JOB_STORE: dict[str, PipelineResult] = {}


def get_job(job_id: str) -> PipelineResult | None:
    """Retrieve a completed job by ID."""
    return _JOB_STORE.get(job_id)


def list_jobs() -> list[dict]:
    """List all completed jobs with summary info."""
    return [
        {
            "job_id": r.job_id,
            "status": "completed",
            "filename": r.filename,
            "annotations_written": r.stats.get("annotations_written", 0),
            "resolution_rate": r.stats.get("resolution_rate", 0.0),
        }
        for r in _JOB_STORE.values()
    ]


def run_pipeline(input_pdf_path: Path, original_filename: str = "unknown.pdf") -> PipelineResult:
    """
    Execute the full aCRF annotation pipeline.

    Mirrors run.py logic exactly:
        1. Parse CRF PDF → extract_crf()
        1.5. Set study context (therapeutic area detection)
        2. Filter noise → is_noise_field()
        3. Resolve: Tier1 (NOT SUBMITTED) first, then Tier0
        4. Write annotated PDF → annotate_pdf()

    Args:
        input_pdf_path: Path to uploaded blank CRF PDF.
        original_filename: Original name of uploaded file.

    Returns:
        PipelineResult with output path, stats, and all mappings.
    """
    job_id = uuid.uuid4().hex[:8]
    output_dir = Path(tempfile.mkdtemp(prefix=f"acrf_{job_id}_"))
    output_pdf_path = output_dir / f"aCRF_annotated_{job_id}.pdf"

    logger.info("Pipeline started", job_id=job_id, filename=original_filename)

    # ══════════════════════════════════════════════════════════
    # STEP 1: Parse CRF PDF
    # ══════════════════════════════════════════════════════════
    parse_result = extract_crf(input_pdf_path)
    all_fields = parse_result.all_fields
    total_fields = len(all_fields)
    total_pages = parse_result.total_pdf_pages
    unique_forms = len({f.form_code for f in all_fields if f.form_code})

    # ══════════════════════════════════════════════════════════
    # STEP 1.5: Set Study Context (therapeutic area detection)
    # ══════════════════════════════════════════════════════════
    all_form_codes = {f.form_code for f in all_fields if f.form_code}
    set_study_context(all_form_codes)

    # ══════════════════════════════════════════════════════════
    # STEP 2: Filter Noise
    # ══════════════════════════════════════════════════════════
    data_fields = [f for f in all_fields if not is_noise_field(f)]
    fields_after_filter = len(data_fields)
    noise_removed = total_fields - fields_after_filter

    # ══════════════════════════════════════════════════════════
    # STEP 3: Resolve SDTM Mappings
    # ══════════════════════════════════════════════════════════
    tier0 = Tier0Rules()
    tier1 = Tier1NotSubmitted()
    _reset_usage_tracking()

    results: list[ResolutionResult] = []
    counters = {
        "tier0_regex": 0,
        "tier0_standards": 0,
        "tier0_az_spec": 0,
        "tier1": 0,
        "unresolved": 0,
    }

    for fld in data_fields:
        # Try Tier 1 (NOT SUBMITTED) first — same order as run.py
        t1 = tier1.resolve(field_label=fld.field_label, form_code=fld.form_code)
        if t1:
            results.append(t1)
            counters["tier1"] += 1
            continue

        # Try Tier 0 (deterministic mapping) — with form_name for inference
        t0 = tier0.resolve(
            form_code=fld.form_code,
            field_label=fld.field_label,
            form_name=getattr(fld, 'form_name', ''),
        )
        if t0:
            results.append(t0)
            if t0.confidence >= 0.98:
                counters["tier0_regex"] += 1
            elif t0.confidence >= 0.92:
                counters["tier0_standards"] += 1
            else:
                counters["tier0_az_spec"] += 1
            continue

        # Unresolved
        results.append(ResolutionResult(
            form_code=fld.form_code,
            field_label=fld.field_label,
            resolved=False,
            tier=ResolutionTier.UNRESOLVED,
            confidence=0.0,
            sdtm_domain="",
            sdtm_variable="",
        ))
        counters["unresolved"] += 1

    resolved_count = fields_after_filter - counters["unresolved"]
    resolution_rate = round(
        resolved_count / fields_after_filter * 100, 1
    ) if fields_after_filter else 0.0

    # ══════════════════════════════════════════════════════════
    # STEP 4: Write Annotated PDF
    # ══════════════════════════════════════════════════════════
    write_stats = annotate_pdf(
        input_pdf_path=input_pdf_path,
        output_pdf_path=output_pdf_path,
        results=results,
        fields=data_fields,
    )

    # ══════════════════════════════════════════════════════════
    # Build Stats
    # ══════════════════════════════════════════════════════════
    stats = {
        "total_pages": total_pages,
        "total_fields_extracted": total_fields,
        "unique_forms": unique_forms,
        "fields_after_noise_filter": fields_after_filter,
        "noise_removed": noise_removed,
        "resolved_count": resolved_count,
        "unresolved_count": counters["unresolved"],
        "resolution_rate": resolution_rate,
        "annotations_written": write_stats.get("total_annotations", 0),
        "pages_annotated": write_stats.get("pages_annotated", 0),
        "not_submitted_count": counters["tier1"],
        "duplicates_skipped": write_stats.get("duplicates_skipped", 0),
        "skipped_no_position": write_stats.get("skipped_no_position", 0),
        "tier0_regex": counters["tier0_regex"],
        "tier0_standards": counters["tier0_standards"],
        "tier0_az_spec": counters["tier0_az_spec"],
    }

    resolved_list = [r for r in results if r.resolved]
    unresolved_list = [r for r in results if not r.resolved]

    pipeline_result = PipelineResult(
        job_id=job_id,
        filename=original_filename,
        output_pdf_path=output_pdf_path,
        input_pdf_path=input_pdf_path,
        stats=stats,
        all_results=results,
        data_fields=data_fields,
        resolved_results=resolved_list,
        unresolved_results=unresolved_list,
    )

    _JOB_STORE[job_id] = pipeline_result

    logger.info(
        "Pipeline complete",
        job_id=job_id,
        resolution_rate=f"{resolution_rate}%",
        annotations=write_stats.get("total_annotations", 0),
    )

    return pipeline_result


# =============================================================================
# ANNOTATION OVERRIDE HELPERS
# =============================================================================

_ANN_RE = re.compile(
    r"^(SUPP)?([A-Z]{2,6})\.([A-Z0-9]+)(?:\s*\(([^)]+)\))?$",
    re.IGNORECASE,
)


def _parse_annotation_string(
    ann: str, form_code: str, field_label: str
) -> ResolutionResult | None:
    """
    Parse a user-supplied annotation string into a ResolutionResult.

    Accepts:
      - "VS.VSORRES"
      - "SUPPVS.QVAL (C66770)"
      - "NOT SUBMITTED"
      - "" (empty → returns None, meaning delete)
    """
    s = ann.strip().upper()
    if not s:
        return None

    if s == "NOT SUBMITTED":
        return ResolutionResult(
            form_code=form_code,
            field_label=field_label,
            resolved=True,
            tier=ResolutionTier.TIER0_EXACT,
            confidence=1.0,
            sdtm_domain="",
            sdtm_variable="",
            is_not_submitted=True,
            is_supplemental=False,
            codelist_code="",
        )

    m = _ANN_RE.match(s)
    if not m:
        return None

    is_supp  = bool(m.group(1))
    domain   = m.group(2)
    variable = m.group(3)
    codelist = m.group(4) or ""

    return ResolutionResult(
        form_code=form_code,
        field_label=field_label,
        resolved=True,
        tier=ResolutionTier.TIER0_EXACT,
        confidence=1.0,
        sdtm_domain=domain,
        sdtm_variable=variable,
        is_supplemental=is_supp,
        is_not_submitted=False,
        codelist_code=codelist,
    )


def _build_result_from_annotations(
    annotations: list[str], form_code: str, field_label: str
) -> ResolutionResult:
    """
    Build a ResolutionResult from a list of annotation strings.

    First string is the primary mapping; remainder become additional_mappings.
    Empty list → unresolved result.
    """
    if not annotations:
        return ResolutionResult(
            form_code=form_code,
            field_label=field_label,
            resolved=False,
            tier=ResolutionTier.UNRESOLVED,
            confidence=0.0,
            sdtm_domain="",
            sdtm_variable="",
        )

    primary = _parse_annotation_string(annotations[0], form_code, field_label)
    if primary is None:
        return ResolutionResult(
            form_code=form_code,
            field_label=field_label,
            resolved=False,
            tier=ResolutionTier.UNRESOLVED,
            confidence=0.0,
            sdtm_domain="",
            sdtm_variable="",
        )

    for ann_str in annotations[1:]:
        r = _parse_annotation_string(ann_str, form_code, field_label)
        if r and not r.is_not_submitted and r.sdtm_domain and r.sdtm_variable:
            primary.additional_mappings.append({
                "domain": r.sdtm_domain,
                "variable": r.sdtm_variable,
                "is_supplemental": r.is_supplemental,
                "codelist_code": r.codelist_code,
            })

    return primary


# =============================================================================
# APPLY EDITS — re-annotate with user overrides
# =============================================================================

def apply_edits(job_id: str, overrides: list) -> PipelineResult:
    """
    Apply user annotation overrides to a completed job and regenerate the PDF.

    Steps:
      1. Retrieve the stored job (data_fields + all_results aligned list)
      2. For each override, find matching rows by (form_code, field_label)
         and replace their ResolutionResult
      3. Re-run annotate_pdf() with the modified results
      4. Update the job store and return the updated PipelineResult
    """
    job = get_job(job_id)
    if not job:
        raise ValueError(f"Job '{job_id}' not found")

    # Work on a mutable copy so we don't mutate the stored list
    updated_results: list[ResolutionResult] = list(job.all_results)

    # Build lookup: (form_code_upper, label_lower) → list of indices in updated_results
    from collections import defaultdict
    index: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, r in enumerate(updated_results):
        key = (r.form_code.upper().strip(), r.field_label.lower().strip())
        index[key].append(i)

    changes_applied = 0
    for override in overrides:
        key = (override.form_code.upper().strip(), override.field_label.lower().strip())
        indices = index.get(key, [])
        if not indices:
            continue

        new_result = _build_result_from_annotations(
            override.annotations, override.form_code, override.field_label
        )
        for i in indices:
            updated_results[i] = new_result

        changes_applied += len(indices)

    # Re-run annotate_pdf with updated results
    new_output_path = job.output_pdf_path.parent / f"aCRF_annotated_{job_id}_v2.pdf"
    try:
        write_stats = annotate_pdf(
            input_pdf_path=job.input_pdf_path,
            output_pdf_path=new_output_path,
            results=updated_results,
            fields=job.data_fields,
        )
    except Exception as e:
        logger.error("Re-annotation failed", job_id=job_id, error=str(e))
        raise

    # Recompute stats
    resolved_list   = [r for r in updated_results if r.resolved or r.is_not_submitted]
    unresolved_list = [r for r in updated_results if not r.resolved and not r.is_not_submitted]
    resolved_count  = sum(1 for r in updated_results if r.resolved and not r.is_not_submitted)
    not_sub_count   = sum(1 for r in updated_results if r.is_not_submitted)
    total_data      = len(updated_results)

    updated_stats = {
        **job.stats,
        "resolved_count":     resolved_count,
        "unresolved_count":   len(unresolved_list),
        "not_submitted_count": not_sub_count,
        "resolution_rate":    round(resolved_count / total_data * 100, 1) if total_data else 0.0,
        "annotations_written": write_stats.get("total_annotations", 0),
        "pages_annotated":    write_stats.get("pages_annotated", 0),
        "duplicates_skipped": write_stats.get("duplicates_skipped", 0),
        "skipped_no_position": write_stats.get("skipped_no_position", 0),
    }

    # Update job in store
    job.output_pdf_path  = new_output_path
    job.all_results      = updated_results
    job.resolved_results = resolved_list
    job.unresolved_results = unresolved_list
    job.stats            = updated_stats

    logger.info(
        "Edits applied",
        job_id=job_id,
        changes=changes_applied,
        annotations=write_stats.get("total_annotations", 0),
    )

    return job