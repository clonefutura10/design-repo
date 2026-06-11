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
import re
import unicodedata

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


def _norm(text: str) -> str:
    """Normalize Unicode whitespace and casing for reliable text matching."""
    text = unicodedata.normalize("NFC", text)
    # Replace non-breaking spaces and other Unicode whitespace variants with regular space
    text = re.sub(r"[\xa0 -​ 　\t]+", " ", text)
    # Normalize typographic quotes
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _enrich_with_positions(page: fitz.Page, fields: list[CRFField]) -> None:
    """
    Add position coordinates to fields by matching their labels to page text.

    Approach:
    - Build two text-position tables from the page: per-line (spans joined) and
      per-block (all lines in a block joined).  The per-block table catches labels
      that were assembled by the field parser from multiple consecutive PDF lines.
    - For each field try (in order):
        1. Exact match on per-line text
        2. Exact match on per-block text
        3. Starts-with / prefix match on per-line text (original behaviour)
        4. Word-overlap match on per-block text (catches partial matches)
        5. Field-number anchor: the standalone 1-3 digit number at the right
           margin is the structural anchor; look it up in a number→y dict built
           from per-line entries that are pure integers in the right-margin band.
    - If nothing matches, leave y=0.0 (annotation is skipped by pdf_writer).
      We do NOT use a proportional fallback — that places annotations at random
      positions unrelated to the actual field location.
    """
    pw = page.rect.width

    # ── Build per-line and per-block text positions ──
    line_positions:  list[tuple[str, float, float, float, float]] = []
    block_positions: list[tuple[str, float, float, float, float]] = []

    for block in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
        if block.get("type") != 0:
            continue
        block_parts = []
        bx0, by0, bx1, by1 = float("inf"), float("inf"), 0.0, 0.0

        for line in block.get("lines", []):
            parts, lx0, ly0, lx1, ly1 = [], float("inf"), float("inf"), 0.0, 0.0
            for span in line.get("spans", []):
                parts.append(span.get("text", ""))
                bb = span.get("bbox", (0, 0, 0, 0))
                lx0 = min(lx0, bb[0]); ly0 = min(ly0, bb[1])
                lx1 = max(lx1, bb[2]); ly1 = max(ly1, bb[3])
            full = "".join(parts).strip()
            if full and lx0 != float("inf"):
                line_positions.append((full, lx0, ly0, lx1, ly1))
                block_parts.append(full)
                bx0 = min(bx0, lx0); by0 = min(by0, ly0)
                bx1 = max(bx1, lx1); by1 = max(by1, ly1)

        if block_parts and bx0 != float("inf"):
            block_text = " ".join(block_parts)
            block_positions.append((block_text, bx0, by0, bx1, by1))

    # ── Field-number → y (right-margin standalone integers only) ──
    num_y: dict[str, float] = {}
    for text, x0, y0, x1, y1 in line_positions:
        t = text.strip()
        if t.isdigit() and 1 <= int(t) <= 999 and x0 > pw * 0.55:
            num_y[t] = y1

    # ── Word-overlap scorer ──
    def _word_overlap(a: str, b: str) -> float:
        wa = set(a.lower().split())
        wb = set(b.lower().split())
        return len(wa & wb) / max(len(wa), len(wb)) if wa and wb else 0.0

    # ── Assign positions ──
    for crf_field in fields:
        label = crf_field.field_label.strip()
        if not label:
            continue

        label_norm = _norm(label)
        best_ratio  = 0.0
        best_line   = None  # (x0,y0,x1,y1)

        # 1 + 3: scan per-line positions (exact, then prefix) with Unicode normalization
        for text, x0, y0, x1, y1 in line_positions:
            tn = _norm(text)
            if tn == label_norm:
                best_line = (x0, y0, x1, y1)
                best_ratio = 1.0
                break
            prefix_len = min(20, len(label_norm), len(tn))
            if prefix_len >= 4 and (
                tn.startswith(label_norm[:prefix_len]) or label_norm.startswith(tn[:prefix_len])
            ):
                min_len = min(len(tn), len(label_norm))
                if min_len > 0:
                    run = sum(1 for a, b in zip(tn, label_norm) if a == b)
                    r = run / min_len
                    if r > best_ratio:
                        best_ratio = r
                        best_line = (x0, y0, x1, y1)

        if best_line:
            crf_field.x = best_line[0]; crf_field.y = best_line[3]
            crf_field.width = best_line[2] - best_line[0]
            crf_field.height = best_line[3] - best_line[1]
            continue

        # 2: per-block exact / containment match (catches multi-line assembled labels)
        for text, x0, y0, x1, y1 in block_positions:
            tn = _norm(text)
            if tn == label_norm or label_norm in tn:
                crf_field.x = x0; crf_field.y = y1
                crf_field.width = x1 - x0; crf_field.height = y1 - y0
                break

        if crf_field.y > 0.0:
            continue

        # 4: word-overlap on block text
        best_score = 0.0
        best_block  = None
        for text, x0, y0, x1, y1 in block_positions:
            score = _word_overlap(label, text)
            if score > best_score:
                best_score = score
                best_block = (x0, y0, x1, y1)
        if best_block and best_score >= 0.55:
            crf_field.x = best_block[0]; crf_field.y = best_block[3]
            crf_field.width = best_block[2] - best_block[0]
            crf_field.height = best_block[3] - best_block[1]
            continue

        # 5: field-number anchor
        if crf_field.field_number:
            fn = str(crf_field.field_number).strip()
            if fn in num_y:
                crf_field.y = num_y[fn]


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