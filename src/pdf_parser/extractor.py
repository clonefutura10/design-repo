"""
Core PDF extraction module for AZ Blank CRF documents.

Orchestrates the complete PDF parsing pipeline:
1. Open PDF and iterate pages
2. Extract text with position information
3. Parse headers for form codes and visit names
4. Identify fields using line classification
5. Build contextual windows
6. Deduplicate across visits (identify unique form-field combinations)

Output: Complete list of CRFField objects ready for the resolution pipeline.
"""

from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field

import fitz  # PyMuPDF

from config.settings import BLANK_CRF_FILE
from src.pdf_parser.header_parser import parse_page_header, PageHeader
from src.pdf_parser.field_identifier import (
    CRFField,
    identify_fields_from_lines,
)
from src.pdf_parser.context_window import build_contextual_windows
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ParsedPage:
    """Complete parsed result for one CRF page."""

    header: PageHeader
    fields: list[CRFField]
    raw_text: str = ""
    pdf_page_index: int = 0


@dataclass
class CRFParseResult:
    """Complete result of parsing the entire CRF PDF."""

    pages: list[ParsedPage] = field(default_factory=list)
    all_fields: list[CRFField] = field(default_factory=list)
    unique_form_fields: dict[str, list[CRFField]] = field(default_factory=dict)

    # Statistics
    total_pdf_pages: int = 0
    total_fields: int = 0
    unique_field_count: int = 0
    unique_forms: int = 0
    forms_detected: dict[str, int] = field(default_factory=dict)  # form_code → page count


def extract_crf(filepath: Path | None = None) -> CRFParseResult:
    """
    Parse an entire AZ Blank CRF PDF.

    This is the main entry point for PDF extraction. Processes every page,
    extracts all fields, builds contextual windows, and identifies unique
    form-field combinations for the resolution pipeline.

    Args:
        filepath: Path to the CRF PDF. Uses configured default if None.

    Returns:
        CRFParseResult containing all parsed data and statistics.
    """
    filepath = filepath or BLANK_CRF_FILE

    if not filepath.exists():
        raise FileNotFoundError(f"CRF PDF not found: {filepath}")

    logger.info("Opening CRF PDF", path=str(filepath))

    doc = fitz.open(str(filepath))
    total_pages = doc.page_count
    logger.info(f"PDF loaded: {total_pages} pages")

    result = CRFParseResult(total_pdf_pages=total_pages)
    all_fields: list[CRFField] = []
    form_page_counts: dict[str, int] = {}

    for page_idx in range(total_pages):
        page = doc[page_idx]

        # Extract text preserving line structure
        raw_text = page.get_text("text")

        if not raw_text.strip():
            continue

        # Parse header
        header = parse_page_header(raw_text, pdf_page_index=page_idx)

        # Track form occurrences
        if header.form_code:
            form_page_counts[header.form_code] = form_page_counts.get(header.form_code, 0) + 1

        # Split into lines for field identification
        lines = raw_text.split("\n")

        # Identify fields — now passing form_name for domain inference
        page_fields = identify_fields_from_lines(
            lines=lines,
            page_index=page_idx,
            form_code=header.form_code,
            form_name=header.form_name,
            folder=header.folder,
        )

        # Build contextual windows
        page_fields = build_contextual_windows(page_fields, window_size=3)

        # Extract position information for annotation placement
        _enrich_with_positions(page, page_fields)

        # Filter out instruction-only entries for the main field list
        data_fields = [f for f in page_fields if not f.is_instruction]

        # Store results
        parsed_page = ParsedPage(
            header=header,
            fields=data_fields,
            raw_text=raw_text,
            pdf_page_index=page_idx,
        )
        result.pages.append(parsed_page)
        all_fields.extend(data_fields)

        # Progress logging
        if (page_idx + 1) % 50 == 0:
            logger.info(
                f"  Parsed {page_idx + 1}/{total_pages} pages, "
                f"{len(all_fields)} fields extracted"
            )

    doc.close()

    # Build unique form-field index
    unique_form_fields = _build_unique_form_fields(all_fields)

    result.all_fields = all_fields
    result.unique_form_fields = unique_form_fields
    result.total_fields = len(all_fields)
    result.unique_field_count = sum(len(fields) for fields in unique_form_fields.values())
    result.unique_forms = len(form_page_counts)
    result.forms_detected = form_page_counts

    logger.info("─" * 60)
    logger.info("CRF PDF parsing COMPLETE")
    logger.info(f"  Total PDF pages:        {total_pages}")
    logger.info(f"  Pages with content:     {len(result.pages)}")
    logger.info(f"  Total fields extracted: {result.total_fields}")
    logger.info(f"  Unique forms:           {result.unique_forms}")
    logger.info(f"  Unique (form+label):    {result.unique_field_count}")
    logger.info("─" * 60)

    return result


def _enrich_with_positions(page: fitz.Page, fields: list[CRFField]) -> None:
    """
    Add position coordinates to fields by matching text against page blocks.

    Uses PyMuPDF's text blocks which provide (x0, y0, x1, y1) bounding boxes.
    Matches each field's label text to its corresponding block position.
    """
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

    # Build a text → position lookup from blocks
    text_positions: list[tuple[str, float, float, float, float]] = []
    for block in blocks:
        if block.get("type") != 0:  # Text blocks only
            continue
        for line in block.get("lines", []):
            line_text_parts = []
            x0 = float("inf")
            y0 = float("inf")
            x1 = 0
            y1 = 0
            for span in line.get("spans", []):
                line_text_parts.append(span.get("text", ""))
                bbox = span.get("bbox", (0, 0, 0, 0))
                x0 = min(x0, bbox[0])
                y0 = min(y0, bbox[1])
                x1 = max(x1, bbox[2])
                y1 = max(y1, bbox[3])
            full_text = "".join(line_text_parts).strip()
            if full_text:
                text_positions.append((full_text, x0, y0, x1, y1))

    # Match fields to positions
    for crf_field in fields:
        if not crf_field.field_label:
            continue

        label_lower = crf_field.field_label.lower().strip()

        # Find best matching position
        best_match = None
        best_ratio = 0.0

        for text, x0, y0, x1, y1 in text_positions:
            text_lower = text.lower().strip()

            # Exact match
            if text_lower == label_lower:
                best_match = (x0, y0, x1, y1)
                break

            # Starts-with match (for multi-line labels)
            if text_lower.startswith(label_lower[:20]) or label_lower.startswith(text_lower[:20]):
                # Compute rough similarity
                min_len = min(len(text_lower), len(label_lower))
                if min_len > 0:
                    match_len = 0
                    for a, b in zip(text_lower, label_lower):
                        if a == b:
                            match_len += 1
                        else:
                            break
                    ratio = match_len / min_len
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_match = (x0, y0, x1, y1)

        if best_match:
            crf_field.x = best_match[0]
            crf_field.y = best_match[3]
            crf_field.width = best_match[2] - best_match[0]
            crf_field.height = best_match[3] - best_match[1]


def _build_unique_form_fields(all_fields: list[CRFField]) -> dict[str, list[CRFField]]:
    """
    Identify unique (form_code + field_label) combinations.

    When the same form appears at multiple visits, only the first occurrence
    of each field needs full resolution. Pattern Memory handles the rest.

    Returns:
        Dict keyed by form_code → list of unique CRFField objects (first occurrence).
    """
    seen: dict[str, set[str]] = {}  # form_code → set of normalized labels
    unique: dict[str, list[CRFField]] = {}  # form_code → list of unique fields

    for fld in all_fields:
        if not fld.form_code or not fld.field_label.strip():
            continue

        code = fld.form_code
        label_norm = fld.field_label.strip().lower()

        if code not in seen:
            seen[code] = set()
            unique[code] = []

        if label_norm not in seen[code]:
            seen[code].add(label_norm)
            unique[code].append(fld)

    return unique