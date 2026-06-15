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
    identify_fields_geometric,
    page_has_field_numbers,
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
    forms_detected: dict[str, int] = field(default_factory=dict)


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

        # Choose the extraction strategy per page:
        #   • numeric field anchors present → number-anchor parser (text-based)
        #   • no anchors (UAT/preview/generic CRFs) → geometry-aware parser,
        #     which separates left-column labels from right-column value options
        #     and sets positions directly from the span boxes.
        if page_has_field_numbers(lines):
            page_fields = identify_fields_from_lines(
                lines=lines,
                page_index=page_idx,
                form_code=header.form_code,
                form_name=header.form_name,
                folder=header.folder,
            )
            page_fields = build_contextual_windows(page_fields, window_size=3)
            # Extract position information for annotation placement
            _enrich_with_positions(page, page_fields)
        else:
            page_fields = identify_fields_geometric(
                positioned_lines=_positioned_lines(page),
                page_index=page_idx,
                form_code=header.form_code,
                form_name=header.form_name,
                folder=header.folder,
            )
            page_fields = build_contextual_windows(page_fields, window_size=3)
            # Geometric parser already assigned x/y from span boxes. Only run
            # the fuzzy position matcher for any field left without a position.
            missing = [f for f in page_fields if not f.y]
            if missing:
                _enrich_with_positions(page, missing)

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
    """
    Normalize Unicode whitespace for reliable text matching.

    CRITICAL: Only replaces actual whitespace-type characters.
    Does NOT use character ranges that could accidentally match
    digits, letters, or punctuation.
    """
    text = unicodedata.normalize("NFC", text)
    # Replace specific Unicode whitespace characters with regular space
    text = text.replace('\xa0', ' ')       # non-breaking space
    text = text.replace('\u2000', ' ')     # en quad
    text = text.replace('\u2001', ' ')     # em quad
    text = text.replace('\u2002', ' ')     # en space
    text = text.replace('\u2003', ' ')     # em space
    text = text.replace('\u2004', ' ')     # three-per-em space
    text = text.replace('\u2005', ' ')     # four-per-em space
    text = text.replace('\u2006', ' ')     # six-per-em space
    text = text.replace('\u2007', ' ')     # figure space
    text = text.replace('\u2008', ' ')     # punctuation space
    text = text.replace('\u2009', ' ')     # thin space
    text = text.replace('\u200a', ' ')     # hair space
    text = text.replace('\u200b', '')      # zero-width space (remove entirely)
    text = text.replace('\u202f', ' ')     # narrow no-break space
    text = text.replace('\u205f', ' ')     # medium mathematical space
    text = text.replace('\u3000', ' ')     # ideographic space
    text = text.replace('\t', ' ')         # tab
    # Normalize typographic quotes
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    # Collapse multiple spaces and lowercase
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return text


def _word_jaccard(a: str, b: str) -> float:
    """Compute Jaccard similarity between word sets of two strings."""
    wa = set(a.split())
    wb = set(b.split())
    if not wa or not wb:
        return 0.0
    intersection = wa & wb
    union = wa | wb
    return len(intersection) / len(union)


def _match_score(label_norm: str, text_norm: str) -> float:
    """
    Compute a match score between a field label and a PDF text line.

    Returns:
        Score from 0.0 (no match) to 1.0 (exact match).
        Scores >= 0.5 are considered viable matches.
    """
    if not label_norm or not text_norm:
        return 0.0

    # Exact match
    if label_norm == text_norm:
        return 1.0

    # One contains the other fully
    if label_norm in text_norm:
        return 0.9 * (len(label_norm) / len(text_norm))
    if text_norm in label_norm:
        return 0.9 * (len(text_norm) / len(label_norm))

    # Prefix match (first N chars)
    prefix_len = min(25, len(label_norm), len(text_norm))
    if prefix_len >= 4:
        if label_norm[:prefix_len] == text_norm[:prefix_len]:
            # How much of the shorter string matches?
            match_len = 0
            for a, b in zip(label_norm, text_norm):
                if a == b:
                    match_len += 1
                else:
                    break
            ratio = match_len / max(len(label_norm), len(text_norm))
            if ratio > 0.5:
                return 0.7 + 0.2 * ratio

    # Word-level Jaccard similarity
    jaccard = _word_jaccard(label_norm, text_norm)
    if jaccard >= 0.5:
        return 0.5 + 0.3 * jaccard

    return 0.0


def _positioned_lines(
    page: fitz.Page,
) -> list[tuple[float, float, float, float, str]]:
    """
    Return per-line span geometry in reading order as ``(x0, y0, x1, y1, text)``.

    Used by the geometry-aware identifier for numberless CRF pages.
    """
    out: list[tuple[float, float, float, float, str]] = []
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE, sort=True)["blocks"]
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            parts: list[str] = []
            x0, y0, x1, y1 = float("inf"), float("inf"), 0.0, 0.0
            for span in line.get("spans", []):
                parts.append(span.get("text", ""))
                bb = span.get("bbox", (0, 0, 0, 0))
                x0 = min(x0, bb[0])
                y0 = min(y0, bb[1])
                x1 = max(x1, bb[2])
                y1 = max(y1, bb[3])
            full = "".join(parts).strip()
            if full and x0 != float("inf"):
                out.append((x0, y0, x1, y1, full))
    return out


def _enrich_with_positions(page: fitz.Page, fields: list[CRFField]) -> None:
    """
    Add position coordinates to fields using SEQUENTIAL CONSUMPTION.

    Key design:
    - PDF text positions are extracted in reading order (top → bottom)
    - Fields are also in reading order (from raw text line extraction)
    - Each PDF position can only be consumed ONCE
    - This ensures repeated labels (e.g., "Result" x8) each get a UNIQUE Y

    Matching strategies (in priority order):
    1. Exact match after normalization (score = 1.0)
    2. Containment match (one string inside the other)
    3. Prefix match (first 25 chars)
    4. Word-overlap Jaccard similarity (≥ 0.5)
    """
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE, sort=True)["blocks"]

    # Build ordered list of text positions (top-to-bottom reading order)
    text_positions: list[tuple[str, float, float, float, float]] = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            parts: list[str] = []
            x0, y0, x1, y1 = float("inf"), float("inf"), 0.0, 0.0
            for span in line.get("spans", []):
                parts.append(span.get("text", ""))
                bb = span.get("bbox", (0, 0, 0, 0))
                x0 = min(x0, bb[0])
                y0 = min(y0, bb[1])
                x1 = max(x1, bb[2])
                y1 = max(y1, bb[3])
            full = "".join(parts).strip()
            if full and x0 != float("inf"):
                text_positions.append((_norm(full), x0, y0, x1, y1))

    # Track which positions have been consumed
    consumed: set[int] = set()

    for crf_field in fields:
        if not crf_field.field_label:
            continue

        label_norm = _norm(crf_field.field_label)
        if not label_norm:
            continue

        best_idx = -1
        best_score = 0.0

        for idx, (tn, x0, y0, x1, y1) in enumerate(text_positions):
            if idx in consumed:
                continue
            if not tn:
                continue

            score = _match_score(label_norm, tn)

            # Exact match — take immediately (greedy for performance)
            if score == 1.0:
                best_idx = idx
                best_score = 1.0
                break

            # Track best non-exact match
            if score > best_score:
                best_score = score
                best_idx = idx

        # Accept match if score is good enough
        if best_idx >= 0 and best_score >= 0.5:
            consumed.add(best_idx)
            _, x0, y0, x1, y1 = text_positions[best_idx]
            crf_field.x = x0
            crf_field.y = y1      # bottom of text line = annotation baseline
            crf_field.width = x1 - x0
            crf_field.height = y1 - y0


def _build_unique_form_fields(all_fields: list[CRFField]) -> dict[str, list[CRFField]]:
    """
    Identify unique (form_code + field_label) combinations.

    When the same form appears at multiple visits, only the first occurrence
    of each field needs full resolution. Pattern Memory handles the rest.

    Returns:
        Dict keyed by form_code → list of unique CRFField objects (first occurrence).
    """
    seen: dict[str, set[str]] = {}
    unique: dict[str, list[CRFField]] = {}

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
