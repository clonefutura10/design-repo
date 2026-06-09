"""
Extract SDTM mappings from annotated reference aCRF PDFs.

Reads FreeText PDF annotation objects and matches them to CRF field labels
by Y-coordinate proximity. Builds cache/learned_mappings.json for Pass 0.

Handles multiple annotation formats:
  - Variable only: "SVSTDTC" (domain from page header)
  - Domain.Variable: "DM.DTHDTC"
  - SUPP format A: "SUPPSV.QVAL where QNAM = VECNTMOD"
  - SUPP format B: "QVAL in SUPPSV where QNAM = VECNTMOD"
  - Qualified: "FAORRES where FATESTCD = COUNT"
  - NOT SUBMITTED: "[NOT SUBMITTED]"
  - No-map instructions: "no need to map..." → NOT SUBMITTED
  - Domain header: "SV = Subject Visits" (sets page domain context)
"""

from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import fitz

from src.utils.text_normalizer import normalize_label_for_lookup
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_INPUT_DIR = PROJECT_ROOT / "input" / "reference_acrfs"
_OUTPUT_FILE = PROJECT_ROOT / "cache" / "learned_mappings.json"

# Maximum Y distance (points) to match annotation to field label
_MAX_Y_DISTANCE = 25.0


# ============================================================================
# ANNOTATION PARSING
# ============================================================================

@dataclass
class ParsedAnnotation:
    """A parsed SDTM annotation from a PDF FreeText object."""
    domain: str = ""
    variable: str = ""
    is_supplemental: bool = False
    is_not_submitted: bool = False
    is_domain_header: bool = False
    is_skip: bool = False
    codelist_code: str = ""
    raw_text: str = ""
    rect_y0: float = 0.0
    rect_y1: float = 0.0
    rect_x0: float = 0.0
    qnam: str = ""


# ============================================================================
# FILTER PATTERNS — Skip or reclassify non-mapping annotations
# ============================================================================

# These are NOT SDTM mappings — skip entirely
_SKIP_PATTERNS = [
    re.compile(r"see\s+page\s+\d+", re.IGNORECASE),
    re.compile(r"for\s+annotations?\s+see", re.IGNORECASE),
    re.compile(r"refer\s+to\s+page", re.IGNORECASE),
    re.compile(r"^\d+\s*of\s*\d+$"),  # Page numbers
    re.compile(r"^page\s*\d+", re.IGNORECASE),
    re.compile(r"^note:", re.IGNORECASE),
    re.compile(r"^version", re.IGNORECASE),
    re.compile(r"^generated\s+on", re.IGNORECASE),
    re.compile(r"^\(version", re.IGNORECASE),
    re.compile(r"^fixed\s*unit:", re.IGNORECASE),
    re.compile(r"^D\d{4}[A-Z]", re.IGNORECASE),  # Study codes
]

# These indicate the field should NOT be submitted — treat as NOT_SUBMITTED
_NOT_SUBMITTED_PATTERNS = [
    re.compile(r"no\s*need\s*to\s*map", re.IGNORECASE),
    re.compile(r"not\s*to\s*be\s*mapped", re.IGNORECASE),
    re.compile(r"no\s*records?\s*shall\s*be\s*submitted", re.IGNORECASE),
    re.compile(r"will\s*not\s*be\s*submitted", re.IGNORECASE),
    re.compile(r"not\s*mapped", re.IGNORECASE),
    re.compile(r"^\[?\s*NOT\s*SUBMITTED\s*\]?$", re.IGNORECASE),
    re.compile(r"not\s+collected", re.IGNORECASE),
    re.compile(r"if\s*checked\s*no.*no\s*records", re.IGNORECASE),
]

# Known domain names for header detection
_DOMAIN_FULL_NAMES = {
    "AE": "ADVERSE EVENTS", "BE": "BIOSPECIMEN", "CE": "CLINICAL EVENTS",
    "CM": "CONCOMITANT MEDICATIONS", "CO": "COMMENTS", "DA": "DRUG ACCOUNTABILITY",
    "DD": "DEATH DETAILS", "DM": "DEMOGRAPHICS", "DS": "DISPOSITION",
    "DV": "PROTOCOL DEVIATIONS", "EC": "EXPOSURE AS COLLECTED", "EG": "ECG",
    "EX": "EXPOSURE", "FA": "FINDINGS ABOUT", "HO": "HEALTHCARE",
    "IE": "INCLUSION", "IS": "IMMUNOGENICITY", "LB": "LABORATORY",
    "MB": "MICROBIOLOGY", "MH": "MEDICAL HISTORY", "MI": "MICROSCOPIC",
    "PC": "PHARMACOKINETIC", "PE": "PHYSICAL EXAMINATION", "PP": "PK PARAMETERS",
    "PR": "PROCEDURES", "QS": "QUESTIONNAIRE", "RE": "RESPIRATORY",
    "RP": "REPRODUCTIVE", "RS": "DISEASE RESPONSE", "SC": "SUBJECT CHARACTERISTICS",
    "SE": "SUBJECT ELEMENTS", "SS": "SUBJECT STATUS", "SU": "SUBSTANCE USE",
    "SV": "SUBJECT VISITS", "TA": "TRIAL ARMS", "TI": "TRIAL INCLUSION",
    "TR": "TUMOR", "TS": "TRIAL SUMMARY", "TU": "TUMOR", "TV": "TRIAL VISITS",
    "VS": "VITAL SIGNS",
}

_VALID_DOMAINS = set(_DOMAIN_FULL_NAMES.keys())

# SDTM variable prefix → domain mapping
_VARIABLE_PREFIX_TO_DOMAIN: dict[str, str] = {
    d: d for d in _VALID_DOMAINS
}

# ============================================================================
# REGEX PATTERNS for annotation parsing
# ============================================================================

_RE_DOMAIN_HEADER = re.compile(r"^([A-Z]{2,6})\s*=\s*(.+)")
_RE_STUDYID = re.compile(r"^STUDYID\s*=", re.IGNORECASE)

# SUPPDM.QVAL where QNAM = "VECNTMOD"
_RE_SUPP_DOT_QVAL = re.compile(
    r"^SUPP([A-Z]{2,6})\.QVAL\s+where\s+QNAM\s*=\s*[\"']?([A-Z][A-Z0-9_]+)[\"']?",
    re.IGNORECASE
)
# QVAL in SUPPSV where QNAM = "VECNTMOD"
_RE_QVAL_IN_SUPP = re.compile(
    r"^QVAL\s+in\s+SUPP([A-Z]{2,6})\s+where\s+QNAM\s*=\s*[\"']?([A-Z][A-Z0-9_]+)[\"']?",
    re.IGNORECASE
)
# FAORRES where FATESTCD = COUNT (qualified variable)
_RE_VAR_WHERE_TESTCD = re.compile(
    r"^([A-Z]{2,8})\s+where\s+([A-Z]{2,8})\s*=\s*[\"']?([A-Z][A-Z0-9_ ]+)[\"']?",
    re.IGNORECASE
)
# Domain.Variable: DM.DTHDTC, SUPPDM.RACEOTH, also with codelist: DM.SEX (C66731)
_RE_DOMAIN_DOT_VAR = re.compile(
    r"^(SUPP)?([A-Z]{2,6})\.([A-Z][A-Z0-9_]{1,20})(?:\s*\(([^)]+)\))?$"
)
# Bare variable: SVSTDTC — must be valid SDTM variable format (2-8 chars, uppercase)
_RE_BARE_VARIABLE = re.compile(
    r"^([A-Z]{2}[A-Z0-9_]{1,18})(?:\s*\(([^)]+)\))?$"
)
# Codelist in parens at end
_RE_CODELIST_SUFFIX = re.compile(r"\(([A-Z]{1,3}\d{4,6})\)\s*$")

# Maximum length for a valid SDTM variable name
_MAX_VARIABLE_LENGTH = 20


def _is_valid_variable(var: str) -> bool:
    """Check if a string looks like a valid SDTM variable name."""
    if not var or len(var) > _MAX_VARIABLE_LENGTH or len(var) < 3:
        return False
    # Must start with 2+ letters, can contain letters/digits/underscore
    if not re.match(r"^[A-Z]{2}[A-Z0-9_]*$", var):
        return False
    # Should have a recognizable domain prefix (2-4 chars)
    for prefix_len in (4, 3, 2):
        if var[:prefix_len] in _VALID_DOMAINS:
            return True
    # Some variables like MEDDRAV, WHODRGV are valid but prefix isn't a domain
    # Accept if it's 3-8 chars and all uppercase
    if 3 <= len(var) <= 12:
        return True
    return False


def parse_annotation_text(raw_text: str, page_domain: str = "") -> ParsedAnnotation:
    """Parse a FreeText annotation's content into structured data."""
    # Take only first line (annotations can have trailing newlines or multi-line noise)
    text = raw_text.strip().split("\n")[0].strip()
    ann = ParsedAnnotation(raw_text=text)

    if not text:
        ann.is_skip = True
        return ann

    # ── Check SKIP patterns first ──
    for pattern in _SKIP_PATTERNS:
        if pattern.search(text):
            ann.is_skip = True
            return ann

    # ── Check NOT SUBMITTED patterns ──
    for pattern in _NOT_SUBMITTED_PATTERNS:
        if pattern.search(text):
            ann.is_not_submitted = True
            return ann

    # ── Study ID (skip) ──
    if _RE_STUDYID.match(text):
        ann.is_skip = True
        return ann

    # ── Domain Header: "SV = Subject Visits" ──
    m = _RE_DOMAIN_HEADER.match(text)
    if m:
        domain_code = m.group(1).upper()
        if domain_code in _VALID_DOMAINS:
            ann.is_domain_header = True
            ann.domain = domain_code
            return ann
        if domain_code == "STUDYID":
            ann.is_skip = True
            return ann

    # ── SUPP format A: SUPPSV.QVAL where QNAM = "VECNTMOD" ──
    m = _RE_SUPP_DOT_QVAL.match(text)
    if m:
        ann.domain = m.group(1).upper()
        ann.variable = m.group(2).upper()
        ann.qnam = ann.variable
        ann.is_supplemental = True
        return ann

    # ── SUPP format B: QVAL in SUPPSV where QNAM = "VECNTMOD" ──
    m = _RE_QVAL_IN_SUPP.match(text)
    if m:
        ann.domain = m.group(1).upper()
        ann.variable = m.group(2).upper()
        ann.qnam = ann.variable
        ann.is_supplemental = True
        return ann

    # ── Qualified variable: FAORRES where FATESTCD = COUNT ──
    m = _RE_VAR_WHERE_TESTCD.match(text)
    if m:
        variable = m.group(1).upper()
        # testcd_var = m.group(2).upper()  # e.g., FATESTCD
        # testcd_val = m.group(3).strip().upper()  # e.g., COUNT

        # Infer domain from variable prefix
        inferred_domain = ""
        for prefix_len in (4, 3, 2):
            prefix = variable[:prefix_len]
            if prefix in _VALID_DOMAINS:
                inferred_domain = prefix
                break

        ann.domain = inferred_domain or page_domain
        ann.variable = variable
        return ann

    # ── Check for codelist suffix ──
    codelist_match = _RE_CODELIST_SUFFIX.search(text)
    if codelist_match:
        ann.codelist_code = codelist_match.group(1)
        text = _RE_CODELIST_SUFFIX.sub("", text).strip()

    # ── Domain.Variable: DM.DTHDTC, SUPPDM.RACEOTH ──
    m = _RE_DOMAIN_DOT_VAR.match(text)
    if m:
        supp_prefix = m.group(1)
        domain = m.group(2).upper()
        variable = m.group(3).upper()

        if domain in _VALID_DOMAINS and _is_valid_variable(variable):
            ann.domain = domain
            ann.variable = variable
            ann.is_supplemental = bool(supp_prefix)
            if m.group(4) and not ann.codelist_code:
                ann.codelist_code = m.group(4).strip()
            return ann

    # ── Bare variable: SVSTDTC, AESTDTC ──
# ── Bare variable: SVSTDTC, AESTDTC ──
    m = _RE_BARE_VARIABLE.match(text)
    if m:
        var_name = m.group(1).upper()

        if _is_valid_variable(var_name):
            # Infer domain from variable prefix
            inferred_domain = ""
            for prefix_len in (4, 3, 2):
                prefix = var_name[:prefix_len]
                if prefix in _VALID_DOMAINS:
                    inferred_domain = prefix
                    break

            # Page domain takes priority for SHORT variables (≤5 chars)
            # where prefix inference is unreliable (e.g. SEX → SE, RACE → ?)
            # For longer variables like SVSTDTC, prefix is reliable
            if page_domain and (len(var_name) <= 5 or inferred_domain != page_domain):
                # Trust page domain when:
                #   - Variable is short (ambiguous prefix) OR
                #   - Inferred domain conflicts with page context
                # But if inferred clearly matches variable structure, keep it
                if len(var_name) <= 5:
                    ann.domain = page_domain
                elif inferred_domain and var_name.startswith(inferred_domain):
                    ann.domain = inferred_domain
                else:
                    ann.domain = page_domain
            else:
                ann.domain = inferred_domain or page_domain

            ann.variable = var_name
            if m.group(2) and not ann.codelist_code:
                ann.codelist_code = m.group(2).strip()
            return ann

    # ── Nothing matched → skip ──
    ann.is_skip = True
    return ann


# ============================================================================
# FIELD LABEL EXTRACTION (from page text)
# ============================================================================

@dataclass
class PageField:
    """A CRF field label found on a page."""
    label: str
    y_position: float
    x_position: float
    form_code: str = ""


# Lines to skip when extracting fields
_FIELD_SKIP_PATTERNS = [
    re.compile(r"^\d+\s*(of\s*\d+)?$"),
    re.compile(r"^Generated\s+On:", re.IGNORECASE),
    re.compile(r"^D\d{4}[A-Z]"),
    re.compile(r"^\(Version", re.IGNORECASE),
    re.compile(r"^Version", re.IGNORECASE),
    re.compile(r"^Form:", re.IGNORECASE),
    re.compile(r"^Folder:", re.IGNORECASE),
    re.compile(r"^Fixed\s*Unit:", re.IGNORECASE),
    re.compile(r"^\(\d+\)$"),  # Just a number in parens
]


def extract_page_fields(page: fitz.Page, form_code: str = "") -> list[PageField]:
    """
    Extract CRF field labels from page text.
    Fields are typically on the LEFT side of the page.
    """
    fields = []
    page_width = page.rect.width
    x_max = page_width * 0.55

    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_text_parts = []
            line_x = None
            line_y = None

            for span in line.get("spans", []):
                x = span["origin"][0]
                y = span["origin"][1]
                text = span["text"].strip()
                size = span["size"]
                font = span.get("font", "")

                if not text:
                    continue

                # Skip header/footer regions
                if size < 6 or y < 40 or y > 750:
                    continue

                # Skip annotation text (italic Arial is typical annotation font)
                if "Italic" in font and "Arial" in font:
                    continue
                if "BoldItali" in font and "Arial" in font:
                    continue

                if x < x_max:
                    line_text_parts.append(text)
                    if line_x is None:
                        line_x = x
                        line_y = y

            if line_text_parts and line_x is not None:
                full_text = " ".join(line_text_parts).strip()

                # Filter noise
                if len(full_text) < 3:
                    continue

                skip = False
                for pattern in _FIELD_SKIP_PATTERNS:
                    if pattern.match(full_text):
                        skip = True
                        break
                if skip:
                    continue

                fields.append(PageField(
                    label=full_text,
                    y_position=line_y,
                    x_position=line_x,
                    form_code=form_code,
                ))

    return fields


# ============================================================================
# FORM CODE DETECTION
# ============================================================================

_RE_FORM_CODE = re.compile(
    r"Form:\s*(?:.*?\(([A-Z][A-Z0-9_]{1,20})\))",
    re.IGNORECASE
)


def detect_form_code(page: fitz.Page) -> str:
    """Try to detect the CRF form code from page header text."""
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                y = span["origin"][1]
                if y > 120:
                    continue
                text = span["text"].strip()
                if "Form:" in text or "form:" in text:
                    m = _RE_FORM_CODE.search(text)
                    if m:
                        return m.group(1).upper()
    return ""


# ============================================================================
# MAIN EXTRACTION
# ============================================================================

@dataclass
class ExtractionStats:
    """Track extraction statistics."""
    files_processed: int = 0
    pages_processed: int = 0
    annotations_found: int = 0
    annotations_parsed: int = 0
    domain_headers_found: int = 0
    not_submitted_found: int = 0
    fields_matched: int = 0
    unmatched_annotations: int = 0
    skipped_annotations: int = 0


def extract_from_pdf(pdf_path: Path, stats: ExtractionStats) -> list[dict]:
    """
    Extract all SDTM mappings from a single annotated aCRF PDF.

    Returns list of mapping dicts.
    """
    doc = fitz.open(str(pdf_path))
    mappings = []
    current_domain = ""

    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        stats.pages_processed += 1

        # ── Detect form code ──
        form_code = detect_form_code(page)

        # ── Get PDF FreeText annotations ──
        annots = list(page.annots()) if page.annots() else []
        if not annots:
            continue

        # ── Parse all annotations on this page ──
        parsed_annotations: list[ParsedAnnotation] = []
        for annot in annots:
            if annot.type[0] != 2:  # Only FreeText
                continue

            content = annot.get_text().strip()
            if not content:
                content = annot.info.get("content", "").strip()
            if not content:
                continue

            stats.annotations_found += 1

            parsed = parse_annotation_text(content, current_domain)
            parsed.rect_x0 = annot.rect.x0
            parsed.rect_y0 = annot.rect.y0
            parsed.rect_y1 = annot.rect.y1

            # Update page domain from headers
            if parsed.is_domain_header:
                current_domain = parsed.domain
                stats.domain_headers_found += 1
                continue

            if parsed.is_skip:
                stats.skipped_annotations += 1
                continue

            # Use current domain if annotation didn't self-identify
            if not parsed.domain and current_domain:
                parsed.domain = current_domain

            parsed_annotations.append(parsed)
            stats.annotations_parsed += 1

        if not parsed_annotations:
            continue

        # ── Extract field labels from page ──
        page_fields = extract_page_fields(page, form_code)
        if not page_fields:
            for pa in parsed_annotations:
                if pa.is_not_submitted:
                    stats.not_submitted_found += 1
                stats.unmatched_annotations += 1
            continue

        # ── Match annotations to fields by Y proximity ──
        for pa in parsed_annotations:
            ann_y_center = (pa.rect_y0 + pa.rect_y1) / 2.0

            best_field = None
            best_distance = _MAX_Y_DISTANCE + 1

            for pf in page_fields:
                distance = abs(pf.y_position - ann_y_center)
                if distance < best_distance:
                    best_distance = distance
                    best_field = pf

            if best_field and best_distance <= _MAX_Y_DISTANCE:
                stats.fields_matched += 1

                if pa.is_not_submitted:
                    stats.not_submitted_found += 1

                mappings.append({
                    "form_code": form_code or best_field.form_code,
                    "field_label": best_field.label,
                    "norm_label": normalize_label_for_lookup(best_field.label),
                    "domain": pa.domain,
                    "variable": pa.variable,
                    "is_supplemental": pa.is_supplemental,
                    "is_not_submitted": pa.is_not_submitted,
                    "codelist_code": pa.codelist_code,
                    "source_file": pdf_path.name,
                    "page": page_idx + 1,
                })
            else:
                stats.unmatched_annotations += 1

    doc.close()
    return mappings


def build_learned_mappings(all_mappings: list[dict]) -> dict:
    """
    Build the learned_mappings.json structure from raw extracted mappings.

    Keys:  form_code|norm_label  and  domain|norm_label  and  |norm_label
    """
    form_key_counts: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "entries": []
    })

    for m in all_mappings:
        form_code = m["form_code"]
        norm_label = m["norm_label"]
        domain = m["domain"]
        variable = m["variable"]

        if not norm_label:
            continue

        entry = {
            "domain": domain,
            "variable": variable,
            "is_supplemental": m["is_supplemental"],
            "is_not_submitted": m["is_not_submitted"],
            "codelist_code": m["codelist_code"],
            "source_file": m["source_file"],
        }

        # Key 1: form_code|label
        if form_code:
            key1 = f"{form_code}|{norm_label}"
            form_key_counts[key1]["count"] += 1
            form_key_counts[key1]["entries"].append(entry)

        # Key 2: domain|label
        if domain:
            key2 = f"{domain}|{norm_label}"
            form_key_counts[key2]["count"] += 1
            form_key_counts[key2]["entries"].append(entry)

        # Key 3: |label (universal)
        key3 = f"|{norm_label}"
        form_key_counts[key3]["count"] += 1
        form_key_counts[key3]["entries"].append(entry)

    # Resolve: pick most common mapping per key
    output_mappings = {}
    for key, data in form_key_counts.items():
        entries = data["entries"]
        if not entries:
            continue

        combo_counts: dict[tuple, int] = defaultdict(int)
        combo_entry: dict[tuple, dict] = {}
        for e in entries:
            combo = (e["domain"], e["variable"], e["is_supplemental"], e["is_not_submitted"])
            combo_counts[combo] += 1
            combo_entry[combo] = e

        best_combo = max(combo_counts, key=combo_counts.get)
        best_entry = combo_entry[best_combo]

        output_mappings[key] = {
            "domain": best_entry["domain"],
            "variable": best_entry["variable"],
            "is_supplemental": best_entry["is_supplemental"],
            "is_not_submitted": best_entry["is_not_submitted"],
            "codelist_code": best_entry["codelist_code"],
            "occurrence_count": combo_counts[best_combo],
        }

    return output_mappings


def main():
    print("=" * 70)
    print("  aCRF REFERENCE EXTRACTION — Building Learned Mappings Cache")
    print("=" * 70)
    print()

    _INPUT_DIR.mkdir(parents=True, exist_ok=True)
    _OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(_INPUT_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"  No PDF files found in: {_INPUT_DIR}")
        print("  → Drop your annotated aCRF PDFs here, then re-run.")
        return

    print(f"  Input:  {_INPUT_DIR} ({len(pdf_files)} PDFs)")
    print(f"  Output: {_OUTPUT_FILE}")
    print()

    logger.info(f"Found {len(pdf_files)} reference aCRF(s) to process")

    stats = ExtractionStats()
    all_mappings: list[dict] = []

    for pdf_path in pdf_files:
        stats.files_processed += 1
        logger.info(f"Processing: {pdf_path.name}")

        try:
            mappings = extract_from_pdf(pdf_path, stats)
            all_mappings.extend(mappings)
        except Exception as e:
            logger.warning(f"Error processing {pdf_path.name}: {e}")
            continue

    # Build final learned mappings
    learned = build_learned_mappings(all_mappings)

    logger.info(
        f"Extraction complete: {stats.annotations_found} annotations, "
        f"{stats.fields_matched} matched, {len(learned)} unique mappings"
    )

    # Save
    output_data = {
        "version": "2.0",
        "extraction_method": "pdf_freetext_annotations",
        "files_processed": stats.files_processed,
        "pages_processed": stats.pages_processed,
        "annotations_found": stats.annotations_found,
        "total_matched": stats.fields_matched,
        "mappings": learned,
    }

    with open(_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    file_size = _OUTPUT_FILE.stat().st_size
    size_str = f"{file_size / 1024:.1f} KB" if file_size > 1024 else f"{file_size} bytes"

    print()
    print("  " + "─" * 50)
    print("  RESULTS:")
    print("  " + "─" * 50)
    print(f"  Files processed:        {stats.files_processed}")
    print(f"  Pages processed:        {stats.pages_processed}")
    print(f"  Annotations found:      {stats.annotations_found}")
    print(f"  Annotations parsed:     {stats.annotations_parsed}")
    print(f"  Domain headers found:   {stats.domain_headers_found}")
    print(f"  Fields matched:         {stats.fields_matched}")
    print(f"  NOT SUBMITTED found:    {stats.not_submitted_found}")
    print(f"  Unmatched annotations:  {stats.unmatched_annotations}")
    print(f"  Skipped annotations:    {stats.skipped_annotations}")
    print(f"  Unique mappings saved:  {len(learned)}")
    print("  " + "─" * 50)
    print(f"  Output: {_OUTPUT_FILE} ({size_str})")

    # Show top mappings
    sorted_mappings = sorted(
        learned.items(),
        key=lambda x: x[1]["occurrence_count"],
        reverse=True,
    )

    print()
    print("  TOP 30 MOST FREQUENT MAPPINGS:")
    print("  " + "─" * 50)
    for key, entry in sorted_mappings[:30]:
        count = entry["occurrence_count"]
        if entry["is_not_submitted"]:
            target = "NOT SUBMITTED"
        else:
            prefix = "SUPP" if entry["is_supplemental"] else ""
            target = f"{prefix}{entry['domain']}.{entry['variable']}"
            if entry["codelist_code"]:
                target += f" ({entry['codelist_code']})"
        print(f"    [{count:3d}x] {key:45s} → {target}")

    print()


if __name__ == "__main__":
    main()