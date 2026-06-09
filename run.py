"""
aCRF Automation — CLI Runner.

Usage:
    python run.py input/my_crf.pdf
    python run.py input/my_crf.pdf --output output/annotated.pdf

Runs the full pipeline:
    1. Parse CRF PDF
    2. Filter noise fields
    3. Resolve SDTM mappings (Tier 1 → Tier 0)
    4. Write annotated PDF
    5. Print summary statistics
"""

from __future__ import annotations
import sys
import argparse
from pathlib import Path

from src.pdf_parser.extractor import extract_crf
from src.resolution.noise_filter import is_noise_field
from src.resolution.tier0_rules import Tier0Rules, _reset_usage_tracking, set_study_context
from src.resolution.tier1_not_submitted import Tier1NotSubmitted
from src.annotator.pdf_writer import annotate_pdf
from src.resolution.models import ResolutionResult, ResolutionTier
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="aCRF Automation — SDTM Annotation Tool")
    parser.add_argument("input_pdf", type=str, help="Path to blank CRF PDF")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output path for annotated PDF")
    args = parser.parse_args()

    input_path = Path(args.input_pdf)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)
    if not input_path.suffix.lower() == ".pdf":
        print(f"ERROR: Input must be a PDF file: {input_path}")
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"aCRF_{input_path.stem}_annotated.pdf"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n{'═' * 60}")
    print(f"  aCRF Automation — SDTM Annotation Pipeline")
    print(f"{'═' * 60}")
    print(f"  Input:  {input_path}")
    print(f"  Output: {output_path}")
    print(f"{'═' * 60}\n")

    # ══════════════════════════════════════════════════════════
    # STEP 1: Parse CRF PDF
    # ══════════════════════════════════════════════════════════
    print("[1/4] Parsing CRF PDF...")
    try:
        parse_result = extract_crf(input_path)
    except Exception as e:
        print(f"ERROR: Failed to parse PDF: {e}")
        logger.error(f"PDF parsing failed: {e}", exc_info=True)
        sys.exit(1)

    all_fields = parse_result.all_fields
    total_fields = len(all_fields)
    total_pages = parse_result.total_pdf_pages
    unique_forms = len({f.form_code for f in all_fields if f.form_code})

    print(f"       → {total_pages} pages, {total_fields} fields, {unique_forms} unique forms")

    # ══════════════════════════════════════════════════════════
    # STEP 1.5: Set Study Context (therapeutic area detection)
    # ══════════════════════════════════════════════════════════
    all_form_codes = {f.form_code for f in all_fields if f.form_code}
    set_study_context(all_form_codes)

    # ══════════════════════════════════════════════════════════
    # STEP 2: Filter Noise
    # ══════════════════════════════════════════════════════════
    print("[2/4] Filtering noise fields...")
    data_fields = [f for f in all_fields if not is_noise_field(f)]
    fields_after_filter = len(data_fields)
    noise_removed = total_fields - fields_after_filter

    print(f"       → {fields_after_filter} data fields ({noise_removed} noise removed)")

    # ══════════════════════════════════════════════════════════
    # STEP 3: Resolve SDTM Mappings
    # ══════════════════════════════════════════════════════════
    print("[3/4] Resolving SDTM mappings...")
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
        # Try Tier 1 (NOT SUBMITTED) first
        t1 = tier1.resolve(field_label=fld.field_label, form_code=fld.form_code)
        if t1:
            results.append(t1)
            counters["tier1"] += 1
            continue

        # Try Tier 0 (deterministic mapping) — with form_name for inference
        t0 = tier0.resolve(
            form_code=fld.form_code,
            field_label=fld.field_label,
            form_name=fld.form_name,
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

    print(f"       → {resolved_count}/{fields_after_filter} resolved ({resolution_rate}%)")
    print(f"         Tier 0 Regex:     {counters['tier0_regex']}")
    print(f"         Tier 0 Standards: {counters['tier0_standards']}")
    print(f"         Tier 0 AZ Spec:   {counters['tier0_az_spec']}")
    print(f"         Tier 1 (N/S):     {counters['tier1']}")
    print(f"         Unresolved:       {counters['unresolved']}")

    # ══════════════════════════════════════════════════════════
    # STEP 4: Write Annotated PDF
    # ══════════════════════════════════════════════════════════
    print("[4/4] Writing annotated PDF...")
    try:
        write_stats = annotate_pdf(
            input_pdf_path=input_path,
            output_pdf_path=output_path,
            results=results,
            fields=data_fields,
        )
    except Exception as e:
        print(f"ERROR: Failed to write annotated PDF: {e}")
        logger.error(f"PDF annotation failed: {e}", exc_info=True)
        sys.exit(1)

    annotations_written = write_stats.get("total_annotations", 0)
    pages_annotated = write_stats.get("pages_annotated", 0)
    duplicates_skipped = write_stats.get("duplicates_skipped", 0)
    skipped_no_pos = write_stats.get("skipped_no_position", 0)

    print(f"       → {annotations_written} annotations on {pages_annotated} pages")
    if duplicates_skipped:
        print(f"         ({duplicates_skipped} duplicates skipped)")
    if skipped_no_pos:
        print(f"         ({skipped_no_pos} skipped — no position data)")

    # ══════════════════════════════════════════════════════════
    # Summary
    # ══════════════════════════════════════════════════════════
    print(f"\n{'═' * 60}")
    print(f"  COMPLETE")
    print(f"{'─' * 60}")
    print(f"  Resolution Rate: {resolution_rate}%")
    print(f"  Annotations:     {annotations_written}")
    print(f"  Output:          {output_path}")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()