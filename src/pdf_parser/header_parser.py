"""
Header parser for AZ EDC Blank CRF pages.

Extracts structured metadata from the consistent header block that appears
on every page of the CRF:
- Study identifier
- Folder (visit name)
- Form name and form short code
- Generation timestamp
- Internal study number
- Version info (first page of each form only)
- Original page number (from "X of Y" footer)

Supports multiple AZ EDC export formats:
- "D5180C00007" (DnCn format)
- "D9186R00001" (DnRn format)
- "D7984C00002" (standard format)
- "D5330C0004B" (with trailing letter)
- "Form: Name (CODE)" with or without space before parenthesis
- Various version line formats
"""

from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class PageHeader:
    """Parsed header metadata from a single CRF page."""

    # Core identifiers
    study_id: str = ""
    folder: str = ""
    form_name: str = ""
    form_code: str = ""
    generated_on: str = ""
    internal_number: str = ""

    # Version (only on first page of a form)
    version: str = ""
    version_date: str = ""

    # Page numbering
    original_page_num: int = 0
    total_pages: int = 0
    pdf_page_index: int = 0

    # Derived flags
    is_first_page_of_form: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Regex Patterns (compiled once, reused for all pages)
# ─────────────────────────────────────────────────────────────────────────────

# "Folder: Visit 1 Screening (Day-42 to -35)"
_RE_FOLDER = re.compile(r"^Folder:\s*(.+)$", re.MULTILINE)

# "Form: Prior Biologics for Asthma (CM1)" or "Form: Visit Date(SV)"
_RE_FORM = re.compile(r"^Form:\s*(.+)$", re.MULTILINE)

# Extract code from parentheses at end — handles optional space before (
# "Prior Biologics for Asthma (CM1)" → "CM1"
# "Visit Date(SV)" → "SV"
# "Lab_Hematology(LB_HEM)" → "LB_HEM"
# "NCFBE Diagnosis History(PR_DIAG)" → "PR_DIAG"
_RE_FORM_CODE = re.compile(r"\(([A-Za-z][A-Za-z0-9_]+)\)\s*$")

# "Generated On: 2020 Aug 19 08:35" or "Generated On: 08 Sep 2025 08:00:36 (GMT)"
_RE_GENERATED = re.compile(r"^Generated On:\s*(.+)$", re.MULTILINE)

# "(24582)" or "(328)" or "(44801)" — internal study number
_RE_INTERNAL_NUM = re.compile(r"^\((\d+)\)\s*$", re.MULTILINE)

# Version patterns — multiple formats observed:
# "(Version:AZ17:05 Date:2017-08-04)"
# "(Version:AZ12:12 Date:2012-12-06)"
# "(Version:TE18:02 Date:2018-03-09)"
# "(Version: AZ2003 2020-03-31)"
# "(Version ON12:10 Date:  2012-10-26)"
# "Version ON13:05 Date:  2013-09-04"  (no parentheses)
_RE_VERSION = re.compile(
    r"\(?Version[:\s]*([A-Z0-9:]+)\s+Date[:\s]+(\d{4}-\d{2}-\d{2})\)?",
    re.IGNORECASE
)
# Alt format: "(Version: AZ2003 2020-03-31)" — date without "Date:" prefix
_RE_VERSION_ALT = re.compile(
    r"\(?Version[:\s]*([A-Z0-9]+)\s+(\d{4}-\d{2}-\d{2})\)?",
    re.IGNORECASE
)

# "9 of 948" — page number at bottom
_RE_PAGE_NUM = re.compile(r"^(\d+)\s+of\s+(\d+)\s*$", re.MULTILINE)

# Study ID line — generalized for multiple formats:
#   D5180C00007_19AUG2020_V30.0: Expanded
#   D5330C0004B_22NOV2022_V17.00: Unique Matrix
#   D9186R00001_V1.0_08SEP2025: All Blank CRF
#   D7984C00002_Version 1.00_20Jun2023: Unique
_RE_STUDY_ID = re.compile(
    r"^(D\d+[A-Z]\d+[A-Z0-9]*[^\n:]*?)(?::\s*(?:Expanded|Unique(?:\s*Matrix)?|All\s*Blank\s*CRF))?\s*$",
    re.MULTILINE
)
# Broader fallback: any line starting with D + digits + letter + digits
_RE_STUDY_ID_FALLBACK = re.compile(
    r"^(D\d{3,}[A-Z]\d{3,}[A-Z0-9]*)",
    re.MULTILINE
)


def parse_page_header(page_text: str, pdf_page_index: int = 0) -> PageHeader:
    """
    Parse the header block from a single CRF page's text.

    Handles multiple AZ EDC export formats robustly.

    Args:
        page_text: Full text content of the PDF page.
        pdf_page_index: Zero-based page index in the PDF.

    Returns:
        PageHeader with all extractable metadata.
    """
    header = PageHeader(pdf_page_index=pdf_page_index)

    # Study ID (try primary pattern first, then fallback)
    match = _RE_STUDY_ID.search(page_text)
    if not match:
        match = _RE_STUDY_ID_FALLBACK.search(page_text)
    if match:
        header.study_id = match.group(1).strip()

    # Folder (visit)
    match = _RE_FOLDER.search(page_text)
    if match:
        header.folder = match.group(1).strip()

    # Form name and code
    match = _RE_FORM.search(page_text)
    if match:
        full_form = match.group(1).strip()
        header.form_name = full_form

        # Extract form code from parentheses at end
        code_match = _RE_FORM_CODE.search(full_form)
        if code_match:
            header.form_code = code_match.group(1).upper()
            # Clean form name (remove the code part)
            header.form_name = full_form[:code_match.start()].strip()

    # Generated On
    match = _RE_GENERATED.search(page_text)
    if match:
        header.generated_on = match.group(1).strip()

    # Internal number
    match = _RE_INTERNAL_NUM.search(page_text)
    if match:
        header.internal_number = match.group(1)

    # Version (indicates first page of a form) — try both patterns
    match = _RE_VERSION.search(page_text)
    if not match:
        match = _RE_VERSION_ALT.search(page_text)
    if match:
        header.version = match.group(1)
        header.version_date = match.group(2)
        header.is_first_page_of_form = True

    # Page numbering (from footer)
    match = _RE_PAGE_NUM.search(page_text)
    if match:
        header.original_page_num = int(match.group(1))
        header.total_pages = int(match.group(2))

    return header