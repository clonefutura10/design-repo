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
    Add position coordinates to fields by matching text against page content.

    Strategy (in order of reliability):
    1. PyMuPDF page.search_for() on the label text — handles multi-line, fonts
    2. search_for on the first meaningful clause of the label (first 40 chars)
    3. search_for on the field number (numeric anchor in AZ CRF)
    4. Block-text similarity matching with word-overlap scoring
    5. Proportional Y fallback so no field is left with y=0.0
    """
    ph = page.rect.height
    pw = page.rect.width

    # ── Build block-text lookup (for fallback) ──
    text_positions: list[tuple[str, float, float, float, float]] = []
    for block in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            parts, x0, y0, x1, y1 = [], float("inf"), float("inf"), 0.0, 0.0
            for span in line.get("spans", []):
                parts.append(span.get("text", ""))
                bb = span.get("bbox", (0, 0, 0, 0))
                x0 = min(x0, bb[0]); y0 = min(y0, bb[1])
                x1 = max(x1, bb[2]); y1 = max(y1, bb[3])
            full = "".join(parts).strip()
            if full and x0 != float("inf"):
                text_positions.append((full, x0, y0, x1, y1))

    # ── Build field-number → y lookup ──
    num_y: dict[str, float] = {}
    for text, x0, y0, x1, y1 in text_positions:
        t = text.strip()
        if t.isdigit() and 1 <= int(t) <= 999:
            num_y[t] = y1

    # ── Word-overlap scorer ──
    def _word_overlap(a: str, b: str) -> float:
        wa = set(a.lower().split())
        wb = set(b.lower().split())
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / max(len(wa), len(wb))

    # ── Assign positions ──
    for i, crf_field in enumerate(fields):
        label = crf_field.field_label.strip()
        if not label:
            continue

        placed = False

        # 1. Direct search_for on full label (up to 50 chars)
        search_str = label[:50]
        hits = page.search_for(search_str, quads=False)
        if hits:
            r = hits[0]
            crf_field.x = r.x0; crf_field.y = r.y1
            crf_field.width = r.width; crf_field.height = r.height
            placed = True

        # 2. search_for on meaningful first clause
        if not placed and len(label) > 10:
            # Use first sentence/clause up to a comma or question mark
            clause = label.split("?")[0].split(",")[0].strip()
            if len(clause) >= 8:
                hits2 = page.search_for(clause[:40], quads=False)
                if hits2:
                    r = hits2[0]
                    crf_field.x = r.x0; crf_field.y = r.y1
                    crf_field.width = r.width; crf_field.height = r.height
                    placed = True

        # 3. search_for on field number
        if not placed and crf_field.field_number:
            fn = str(crf_field.field_number).strip()
            hits3 = page.search_for(fn, quads=False)
            # Filter hits to left-side / right-side (field numbers are at right margin in AZ CRF)
            right_hits = [h for h in hits3 if h.x0 > pw * 0.55]
            target = right_hits[0] if right_hits else (hits3[0] if hits3 else None)
            if target:
                crf_field.x = 0.0; crf_field.y = target.y1
                crf_field.width = pw * 0.55; crf_field.height = target.height
                placed = True
            elif fn in num_y:
                crf_field.y = num_y[fn]
                placed = True

        # 4. Block-text word-overlap fallback
        if not placed:
            best_score = 0.0
            best_pos = None
            for text, x0, y0, x1, y1 in text_positions:
                score = _word_overlap(label, text)
                if score > best_score:
                    best_score = score
                    best_pos = (x0, y0, x1, y1)
            if best_pos and best_score >= 0.5:
                crf_field.x = best_pos[0]; crf_field.y = best_pos[3]
                crf_field.width = best_pos[2] - best_pos[0]
                crf_field.height = best_pos[3] - best_pos[1]
                placed = True

        # 5. Proportional Y fallback — never leave y=0.0
        if not placed or crf_field.y <= 0.0:
            top = 60.0
            bottom = ph - 30.0
            step = (bottom - top) / max(len(fields), 1)
            crf_field.y = top + i * step + step * 0.5
            if crf_field.x == 0.0:
                crf_field.x = 10.0


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