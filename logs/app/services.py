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

from src.pdf_parser.extractor import extract_crf
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
    stats: dict[str, Any]
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
        stats=stats,
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