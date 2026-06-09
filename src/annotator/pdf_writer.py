"""
Professional aCRF PDF Annotation Writer — CDISC industry-standard output.

Visual features matching real annotated CRF conventions:
- Right-margin annotations separated by a thin vertical rule
- Leader tick-lines connecting each annotation to its field row
- Colour-coded boxes by SDTM domain class (Events/Interventions/Findings/Special…)
- Stacked separate boxes for multi-domain mappings
- Domain dataset-name headers at top-right of every form page
- NOT SUBMITTED in distinct grey with dashed border
- Supplemental domains rendered as SUPPXX.VARIABLE
- Hierarchical PDF bookmarks: domain-class → form
- Legend page appended at the end of the output PDF
- Adaptive font sizing for dense pages
- Robust per-page overlap avoidance
"""

from __future__ import annotations

import math
from pathlib import Path
from collections import defaultdict
from typing import Optional

import fitz  # pymupdf

from src.pdf_parser.field_identifier import CRFField
from src.resolution.models import ResolutionResult
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Style Configuration
# =============================================================================

_FONT_NAME          = "helv"
_FONT_NAME_BOLD     = "hebo"        # Helvetica Bold (built-in PDF font)

_FONT_SIZE          = 7.0           # Normal pages
_FONT_SIZE_DENSE    = 6.0           # Dense pages (> threshold annotations)
_HEADER_FONT_SIZE   = 7.5
_LEGEND_FONT_SIZE   = 8.0

_BORDER_WIDTH       = 0.8
_BOX_PADDING_X      = 3.0
_BOX_PADDING_Y      = 2.0
_MULTI_BOX_SPACING  = 2.5          # Vertical gap between stacked domain boxes

_SEPARATOR_X_RATIO  = 0.555        # Thin vertical rule separating content / annotations
_ANNOTATION_X_RATIO = 0.565        # Left edge of annotation boxes (just right of separator)

_TICK_LENGTH        = 6.0          # Length of horizontal tick from separator to box
_TICK_COLOUR        = (0.65, 0.65, 0.65)
_SEPARATOR_COLOUR   = (0.60, 0.60, 0.60)
_SEPARATOR_WIDTH    = 0.4

_PAGE_TOP_MARGIN    = 52.0
_PAGE_BOTTOM_MARGIN = 28.0
_DENSE_THRESHOLD    = 14           # Pages with more annotations use smaller font

_TEXT_COLOUR           = (0.05, 0.05, 0.05)
_NOT_SUB_TEXT          = (0.38, 0.38, 0.38)
_NOT_SUB_BORDER        = (0.52, 0.52, 0.52)
_NOT_SUB_FILL          = (0.95, 0.95, 0.95)

_HEADER_BAR_HEIGHT  = 11.0         # Height of the dataset-name header bar


# =============================================================================
# SDTM Domain Metadata
# =============================================================================

_DOMAIN_NAMES: dict[str, str] = {
    "AE":   "ADVERSE EVENTS",
    "BE":   "BIOSPECIMEN EVENTS",
    "CE":   "CLINICAL EVENTS",
    "CM":   "CONCOMITANT MEDICATIONS",
    "CO":   "COMMENTS",
    "DD":   "DEATH DETAILS",
    "DM":   "DEMOGRAPHICS",
    "DS":   "DISPOSITION",
    "EC":   "EXPOSURE AS COLLECTED",
    "EG":   "ECG TEST RESULTS",
    "EX":   "EXPOSURE",
    "FA":   "FINDINGS ABOUT",
    "FACE": "FINDINGS ABOUT – CLINICAL EVENTS",
    "FAHO": "FINDINGS ABOUT – HEALTHCARE ENCOUNTERS",
    "HO":   "HEALTHCARE ENCOUNTERS",
    "IE":   "INCLUSION / EXCLUSION CRITERIA",
    "IS":   "IMMUNOGENICITY SPECIMEN",
    "LB":   "LABORATORY TEST RESULTS",
    "MB":   "MICROBIOLOGY SPECIMEN",
    "MH":   "MEDICAL HISTORY",
    "PC":   "PHARMACOKINETICS CONCENTRATIONS",
    "PE":   "PHYSICAL EXAMINATION",
    "PR":   "PROCEDURES",
    "QS":   "QUESTIONNAIRES",
    "RE":   "RESPIRATORY SYSTEM FINDINGS",
    "RP":   "REPRODUCTIVE SYSTEM FINDINGS",
    "RS":   "DISEASE RESPONSE",
    "SC":   "SUBJECT CHARACTERISTICS",
    "SU":   "SUBSTANCE USE",
    "SV":   "SUBJECT VISITS",
    "TI":   "TRIAL INCLUSION / EXCLUSION",
    "TR":   "TUMOR / LESION RESULTS",
    "TU":   "TUMOR IDENTIFICATION",
    "VS":   "VITAL SIGNS",
    # Supplemental
    "SUPPDM": "SUPPLEMENTAL DEMOGRAPHICS",
    "SUPPAE": "SUPPLEMENTAL ADVERSE EVENTS",
    "SUPPCM": "SUPPLEMENTAL CONCOMITANT MEDICATIONS",
    "SUPPCE": "SUPPLEMENTAL CLINICAL EVENTS",
    "SUPPEC": "SUPPLEMENTAL EXPOSURE AS COLLECTED",
    "SUPPEG": "SUPPLEMENTAL ECG TEST RESULTS",
    "SUPPEX": "SUPPLEMENTAL EXPOSURE",
    "SUPPFA": "SUPPLEMENTAL FINDINGS ABOUT",
    "SUPPHO": "SUPPLEMENTAL HEALTHCARE ENCOUNTERS",
    "SUPPIE": "SUPPLEMENTAL INCLUSION / EXCLUSION",
    "SUPPIS": "SUPPLEMENTAL IMMUNOGENICITY SPECIMEN",
    "SUPPLB": "SUPPLEMENTAL LABORATORY TEST RESULTS",
    "SUPPMH": "SUPPLEMENTAL MEDICAL HISTORY",
    "SUPPPC": "SUPPLEMENTAL PK CONCENTRATIONS",
    "SUPPPR": "SUPPLEMENTAL PROCEDURES",
    "SUPPQS": "SUPPLEMENTAL QUESTIONNAIRES",
    "SUPPSU": "SUPPLEMENTAL SUBSTANCE USE",
    "SUPPVS": "SUPPLEMENTAL VITAL SIGNS",
}

# Domain class groupings (for bookmarks and legend ordering)
_DOMAIN_CLASSES: dict[str, list[str]] = {
    "Events":        ["AE", "CE", "DD", "HO", "MH"],
    "Interventions": ["CM", "EC", "EX", "PR", "SU"],
    "Findings":      ["BE", "EG", "FA", "FACE", "FAHO", "IS", "LB", "MB", "PC", "PE", "QS", "RE", "RP", "VS"],
    "Special":       ["CO", "DM", "DS", "IE", "SC", "SV", "TI"],
    "Oncology":      ["RS", "TR", "TU"],
}

def _domain_class(domain: str) -> str:
    d = domain.upper()
    if d.startswith("SUPP"):
        d = d[4:]
    for cls, members in _DOMAIN_CLASSES.items():
        if d in members:
            return cls
    return "Other"


def _get_domain_full_name(domain: str) -> str:
    d = domain.upper()
    return _DOMAIN_NAMES.get(d, d)


# =============================================================================
# Domain Colour Map  (border_rgb, fill_rgb)
# =============================================================================

# Colours chosen to match common CDISC colour-coding conventions used by
# major pharma sponsors (FDA CDER guidance-compliant palette).

_DOMAIN_COLOURS: dict[str, tuple[tuple[float,float,float], tuple[float,float,float]]] = {
    # Events — warm red
    "AE":   ((0.72, 0.08, 0.08), (1.00, 0.92, 0.92)),
    "CE":   ((0.72, 0.08, 0.08), (1.00, 0.92, 0.92)),
    "DD":   ((0.72, 0.08, 0.08), (1.00, 0.92, 0.92)),
    "HO":   ((0.72, 0.08, 0.08), (1.00, 0.92, 0.92)),
    "MH":   ((0.72, 0.08, 0.08), (1.00, 0.92, 0.92)),

    # Interventions — forest green
    "CM":   ((0.06, 0.45, 0.06), (0.90, 1.00, 0.90)),
    "EC":   ((0.06, 0.45, 0.06), (0.90, 1.00, 0.90)),
    "EX":   ((0.06, 0.45, 0.06), (0.90, 1.00, 0.90)),
    "PR":   ((0.06, 0.45, 0.06), (0.90, 1.00, 0.90)),
    "SU":   ((0.06, 0.45, 0.06), (0.90, 1.00, 0.90)),

    # Findings — royal blue
    "BE":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),
    "EG":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),
    "FA":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),
    "FACE": ((0.00, 0.44, 0.52), (0.88, 0.98, 1.00)),
    "FAHO": ((0.00, 0.44, 0.52), (0.88, 0.98, 1.00)),
    "IS":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),
    "LB":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),
    "MB":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),
    "PC":   ((0.00, 0.40, 0.40), (0.88, 0.98, 0.98)),
    "PE":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),
    "QS":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),
    "RE":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),
    "RP":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),
    "VS":   ((0.06, 0.12, 0.68), (0.91, 0.93, 1.00)),

    # Special Purpose — medium purple
    "CO":   ((0.42, 0.06, 0.58), (0.95, 0.91, 1.00)),
    "DM":   ((0.42, 0.06, 0.58), (0.95, 0.91, 1.00)),
    "DS":   ((0.42, 0.06, 0.58), (0.95, 0.91, 1.00)),
    "IE":   ((0.42, 0.06, 0.58), (0.95, 0.91, 1.00)),
    "SC":   ((0.42, 0.06, 0.58), (0.95, 0.91, 1.00)),
    "SV":   ((0.42, 0.06, 0.58), (0.95, 0.91, 1.00)),
    "TI":   ((0.42, 0.06, 0.58), (0.95, 0.91, 1.00)),

    # Oncology — dark maroon
    "RS":   ((0.52, 0.00, 0.18), (1.00, 0.90, 0.93)),
    "TR":   ((0.52, 0.00, 0.18), (1.00, 0.90, 0.93)),
    "TU":   ((0.52, 0.00, 0.18), (1.00, 0.90, 0.93)),
}

_DEFAULT_BORDER = (0.35, 0.35, 0.35)
_DEFAULT_FILL   = (0.95, 0.95, 0.95)


def _get_domain_colours(
    domain: str,
) -> tuple[tuple[float,float,float], tuple[float,float,float]]:
    d = domain.upper()
    if d in _DOMAIN_COLOURS:
        return _DOMAIN_COLOURS[d]
    if d.startswith("SUPP"):
        base = d[4:]
        if base in _DOMAIN_COLOURS:
            return _DOMAIN_COLOURS[base]
    return (_DEFAULT_BORDER, _DEFAULT_FILL)


# =============================================================================
# Annotation Entry Builder — one entry per SDTM variable mapping
# =============================================================================

def _build_annotations_list(result: ResolutionResult) -> list[dict]:
    """
    Return a list of annotation dicts (one per SDTM variable) for a field.

    Each dict: {text, domain, is_not_submitted, is_supp}
    """
    annotations: list[dict] = []

    if result.is_not_submitted:
        annotations.append({
            "text": "NOT SUBMITTED",
            "domain": "",
            "is_not_submitted": True,
            "is_supp": False,
        })
        return annotations

    if not result.sdtm_domain or not result.sdtm_variable:
        return annotations

    # Primary mapping
    domain = result.sdtm_domain.upper()
    is_supp = result.is_supplemental
    if is_supp:
        prefix = domain if domain.startswith("SUPP") else f"SUPP{domain}"
        text = f"{prefix}.{result.sdtm_variable}"
    else:
        text = f"{domain}.{result.sdtm_variable}"

    if result.codelist_code:
        text += f" ({result.codelist_code})"

    base_domain = domain[4:] if domain.startswith("SUPP") else domain

    annotations.append({
        "text": text,
        "domain": base_domain,
        "is_not_submitted": False,
        "is_supp": is_supp,
    })

    # Additional mappings
    for mapping in getattr(result, "additional_mappings", None) or []:
        add_domain   = (mapping.get("domain") or mapping.get("sdtm_domain", "")).upper()
        add_variable = mapping.get("variable") or mapping.get("sdtm_variable", "")
        add_codelist = mapping.get("codelist") or mapping.get("codelist_code", "")
        add_is_supp  = mapping.get("is_supp", False) or mapping.get("is_supplemental", False)

        if not add_domain or not add_variable:
            continue

        if add_is_supp:
            prefix = add_domain if add_domain.startswith("SUPP") else f"SUPP{add_domain}"
            add_text = f"{prefix}.{add_variable}"
        else:
            add_text = f"{add_domain}.{add_variable}"

        if add_codelist:
            add_text += f" ({add_codelist})"

        ann_domain = add_domain[4:] if add_domain.startswith("SUPP") else add_domain

        annotations.append({
            "text": add_text,
            "domain": ann_domain,
            "is_not_submitted": False,
            "is_supp": add_is_supp,
        })

    return annotations


# =============================================================================
# Page Domain Helpers
# =============================================================================

def _get_all_domains_for_page(
    results_on_page: list[ResolutionResult],
    form_code: str = "",
) -> list[str]:
    """Return unique base domains for a page, ordered by frequency."""
    counts: dict[str, int] = defaultdict(int)

    if form_code:
        try:
            from src.resolution.tier0_rules import _get_domain_for_form
            mapped = _get_domain_for_form(form_code)
            if mapped:
                counts[mapped] += 1000
        except Exception:
            pass

    for r in results_on_page:
        if not r.resolved or r.is_not_submitted:
            continue
        if r.sdtm_domain:
            d = r.sdtm_domain.upper()
            d = d[4:] if d.startswith("SUPP") else d
            counts[d] += 1
        for m in getattr(r, "additional_mappings", None) or []:
            ad = (m.get("domain") or m.get("sdtm_domain", "")).upper()
            if ad:
                ad = ad[4:] if ad.startswith("SUPP") else ad
                counts[ad] += 1

    return [d for d, _ in sorted(counts.items(), key=lambda x: -x[1])]


# =============================================================================
# Dataset Header  (top-right of form page)
# =============================================================================

def _draw_dataset_headers(
    page: fitz.Page,
    domains: list[str],
    ann_x: float,
    page_width: float,
) -> float:
    """
    Draw stacked colour-coded dataset-name bars at the top-right margin.
    Returns the Y coordinate just below the last header bar.
    """
    if not domains:
        return _PAGE_TOP_MARGIN

    y = 38.0
    right_edge = page_width - 4.0

    for domain in domains:
        full_name    = _get_domain_full_name(domain)
        header_text  = f"  {domain}  —  {full_name}  "
        border_c, fill_c = _get_domain_colours(domain)

        # Slightly darker fill for the header bar
        bar_fill = (
            max(0.0, fill_c[0] - 0.06),
            max(0.0, fill_c[1] - 0.06),
            max(0.0, fill_c[2] - 0.06),
        )

        bar_rect = fitz.Rect(ann_x - 2, y, right_edge, y + _HEADER_BAR_HEIGHT)

        page.draw_rect(bar_rect, color=border_c, fill=bar_fill, width=0.9, overlay=True)

        page.insert_text(
            fitz.Point(ann_x + 2, y + _HEADER_BAR_HEIGHT - 2.5),
            header_text,
            fontsize=_HEADER_FONT_SIZE,
            fontname=_FONT_NAME_BOLD,
            color=border_c,
        )

        y += _HEADER_BAR_HEIGHT + 2.0

    return y + 2.0  # return bottom edge of last header


# =============================================================================
# Separator line and tick marks
# =============================================================================

def _draw_separator_line(page: fitz.Page, sep_x: float, top_y: float, bottom_y: float):
    """Draw the thin vertical rule that separates CRF content from annotations."""
    page.draw_line(
        fitz.Point(sep_x, top_y),
        fitz.Point(sep_x, bottom_y),
        color=_SEPARATOR_COLOUR,
        width=_SEPARATOR_WIDTH,
    )


def _draw_tick(page: fitz.Page, sep_x: float, ann_x: float, y: float):
    """Draw a short horizontal tick from the separator to the annotation box."""
    page.draw_line(
        fitz.Point(sep_x, y),
        fitz.Point(ann_x - _BOX_PADDING_X, y),
        color=_TICK_COLOUR,
        width=0.3,
    )


# =============================================================================
# Overlap Tracker
# =============================================================================

class _OverlapTracker:
    """Tracks placed annotation spans per page and returns non-overlapping Y slots."""

    def __init__(self):
        self._occupied: dict[int, list[tuple[float, float]]] = defaultdict(list)
        self._placed:   dict[int, set[str]]                  = defaultdict(set)

    def is_duplicate(self, page_idx: int, text: str) -> bool:
        if text in self._placed[page_idx]:
            return True
        self._placed[page_idx].add(text)
        return False

    def find_slot(
        self,
        page_idx: int,
        desired_y: float,
        box_height: float,
        page_height: float,
    ) -> float:
        min_y  = _PAGE_TOP_MARGIN + box_height
        max_y  = page_height - _PAGE_BOTTOM_MARGIN
        gap    = 1.5

        desired_y = max(min_y, min(desired_y, max_y))

        def _overlaps(y: float) -> bool:
            top = y - box_height - gap
            bot = y + gap
            for (ot, ob) in self._occupied[page_idx]:
                if top < ob and bot > ot:
                    return True
            return False

        if not _overlaps(desired_y):
            self._mark(page_idx, desired_y, box_height, gap)
            return desired_y

        # Sweep downward first, then upward
        for direction in (1, -1):
            y = desired_y
            for _ in range(80):
                y += direction * (box_height + gap)
                if y > max_y or y < min_y:
                    break
                if not _overlaps(y):
                    self._mark(page_idx, y, box_height, gap)
                    return y

        # Fallback: stack at bottom
        fallback = max_y - len(self._occupied[page_idx]) * (box_height + gap)
        self._mark(page_idx, max(min_y, fallback), box_height, gap)
        return max(min_y, fallback)

    def _mark(self, page_idx: int, y: float, h: float, gap: float):
        self._occupied[page_idx].append((y - h - gap, y + gap))

    def reserve_top(self, page_idx: int, until_y: float):
        """Reserve the top band (used by dataset headers)."""
        self._occupied[page_idx].append((_PAGE_TOP_MARGIN - 20, until_y))


# =============================================================================
# Legend Page
# =============================================================================

def _append_legend_page(doc: fitz.Document, page_width: float, page_height: float):
    """Append a colour-legend page at the end of the PDF."""
    page = doc.new_page(width=page_width, height=page_height)

    # Title
    page.insert_text(
        fitz.Point(36, 44),
        "SDTM Annotation Colour Legend",
        fontsize=14,
        fontname=_FONT_NAME_BOLD,
        color=(0.10, 0.10, 0.10),
    )
    page.insert_text(
        fitz.Point(36, 56),
        "Colour coding applied to annotated CRF (aCRF) variable annotations by SDTM domain class",
        fontsize=8,
        fontname=_FONT_NAME,
        color=(0.40, 0.40, 0.40),
    )

    # Separator line under title
    page.draw_line(
        fitz.Point(36, 60), fitz.Point(page_width - 36, 60),
        color=(0.70, 0.70, 0.70), width=0.5,
    )

    col_x      = [38.0, 310.0]   # Two columns
    row_height = 16.0
    box_w      = 18.0
    box_h      = 10.0
    y          = 76.0
    col        = 0

    class_label_written: set[str] = set()

    for cls_name, members in _DOMAIN_CLASSES.items():
        # Class heading
        if cls_name not in class_label_written:
            x = col_x[col]
            page.insert_text(
                fitz.Point(x, y),
                cls_name.upper(),
                fontsize=8,
                fontname=_FONT_NAME_BOLD,
                color=(0.20, 0.20, 0.20),
            )
            y += row_height * 0.7
            class_label_written.add(cls_name)

        for domain in members:
            if y > page_height - 50:
                # Wrap to next column
                col = min(col + 1, len(col_x) - 1)
                y   = 76.0

            x = col_x[col]
            border_c, fill_c = _get_domain_colours(domain)

            swatch_rect = fitz.Rect(x, y - box_h + 1, x + box_w, y + 1)
            page.draw_rect(swatch_rect, color=border_c, fill=fill_c, width=0.7)

            full = _get_domain_full_name(domain)
            page.insert_text(
                fitz.Point(x + box_w + 5, y),
                f"{domain}  —  {full}",
                fontsize=7.5,
                fontname=_FONT_NAME,
                color=(0.10, 0.10, 0.10),
            )
            y += row_height

        y += row_height * 0.5  # Extra gap between classes

    # NOT SUBMITTED swatch
    x = col_x[col]
    if y > page_height - 50:
        col = min(col + 1, len(col_x) - 1)
        y   = 76.0
        x   = col_x[col]

    page.insert_text(
        fitz.Point(x, y),
        "OTHER",
        fontsize=8,
        fontname=_FONT_NAME_BOLD,
        color=(0.20, 0.20, 0.20),
    )
    y += row_height * 0.7

    ns_rect = fitz.Rect(x, y - box_h + 1, x + box_w, y + 1)
    page.draw_rect(ns_rect, color=_NOT_SUB_BORDER, fill=_NOT_SUB_FILL, width=0.7,
                   dashes="[2 2] 0")
    page.insert_text(
        fitz.Point(x + box_w + 5, y),
        "NOT SUBMITTED  —  Field not collected / derived / internal",
        fontsize=7.5,
        fontname=_FONT_NAME,
        color=(0.10, 0.10, 0.10),
    )
    y += row_height * 1.5

    # Footer note
    page.draw_line(
        fitz.Point(36, page_height - 42), fitz.Point(page_width - 36, page_height - 42),
        color=(0.75, 0.75, 0.75), width=0.4,
    )
    page.insert_text(
        fitz.Point(36, page_height - 32),
        "Supplemental Qualifier variables are annotated with the SUPP-prefixed dataset name (e.g. SUPPVS.QVAL).",
        fontsize=7,
        fontname=_FONT_NAME,
        color=(0.45, 0.45, 0.45),
    )
    page.insert_text(
        fitz.Point(36, page_height - 22),
        "This aCRF was generated automatically. Verify all annotations against the study SDTM specification.",
        fontsize=7,
        fontname=_FONT_NAME,
        color=(0.45, 0.45, 0.45),
    )


# =============================================================================
# Hierarchical Bookmark Builder
# =============================================================================

def _build_toc(
    form_first_page: dict[str, tuple[int, str]],
    form_domains: dict[str, str],
) -> list[list]:
    """
    Build a three-level TOC:
      Level 1 — Domain class (Events / Interventions / Findings …)
      Level 2 — Domain (VS, LB …)
      Level 3 — Form (form_code, page)
    """
    # Group forms by domain then domain class
    class_domain_forms: dict[str, dict[str, list[tuple[str, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for form_code, (page_idx, _) in sorted(
        form_first_page.items(), key=lambda x: x[1][0]
    ):
        domain = form_domains.get(form_code, "")
        cls    = _domain_class(domain) if domain else "Other"
        class_domain_forms[cls][domain or "??"].append((form_code, page_idx))

    toc: list[list] = []

    class_order = list(_DOMAIN_CLASSES.keys()) + ["Other"]
    for cls in class_order:
        if cls not in class_domain_forms:
            continue
        toc.append([1, cls, list(class_domain_forms[cls].values())[0][0][1] + 1])
        for domain, forms in sorted(
            class_domain_forms[cls].items(),
            key=lambda x: x[0],
        ):
            full = _get_domain_full_name(domain)
            toc.append([2, f"{domain} — {full}", forms[0][1] + 1])
            for form_code, pg in forms:
                toc.append([3, form_code, pg + 1])

    return toc


# =============================================================================
# Main annotate_pdf function
# =============================================================================

def annotate_pdf(
    input_pdf_path: Path,
    output_pdf_path: Path,
    results: list[ResolutionResult],
    fields: list[CRFField],
    font_size: float = _FONT_SIZE,
) -> dict:
    """
    Write industry-standard aCRF annotations onto a blank CRF PDF.

    Produces:
    - Right-margin colour-coded annotation boxes with separator rule + tick lines
    - Stacked boxes for multi-domain / multi-variable mappings
    - Domain dataset-name header bars per form page
    - Hierarchical PDF bookmarks (class → domain → form)
    - Legend page appended at the end
    """
    if len(results) != len(fields):
        raise ValueError(
            f"results ({len(results)}) and fields ({len(fields)}) length mismatch"
        )

    doc = fitz.open(str(input_pdf_path))

    stats: dict = {
        "total_annotations":  0,
        "pages_annotated":    set(),
        "not_submitted":      0,
        "skipped_no_position": 0,
        "duplicates_skipped": 0,
        "multi_domain_fields": 0,
    }

    tracker = _OverlapTracker()

    # ── Pre-group by page ──
    page_results:     dict[int, list[ResolutionResult]] = defaultdict(list)
    page_ann_count:   dict[int, int]                    = defaultdict(int)
    page_form_codes:  dict[int, set[str]]               = defaultdict(set)

    for field, result in zip(fields, results):
        if field.page_index is not None:
            page_results[field.page_index].append(result)
            if field.form_code:
                page_form_codes[field.page_index].add(field.form_code)
            if result.resolved or result.is_not_submitted:
                page_ann_count[field.page_index] += 1

    # ── Track form metadata ──
    form_header_written: set[str]                          = set()
    form_first_page:     dict[str, tuple[int, str]]        = {}
    form_primary_domain: dict[str, str]                    = {}

    for field, result in zip(fields, results):
        fc = field.form_code
        if fc and fc not in form_first_page and field.page_index is not None:
            form_first_page[fc] = (field.page_index, fc)
        if fc and fc not in form_primary_domain and result.sdtm_domain:
            d = result.sdtm_domain.upper()
            form_primary_domain[fc] = d[4:] if d.startswith("SUPP") else d

    # ── Track which pages need a separator line ──
    pages_with_separator: set[int] = set()

    # ── Write annotations ──
    for field, result in zip(fields, results):

        if not result.resolved and not result.is_not_submitted:
            continue

        page_idx = field.page_index
        y        = field.y

        if page_idx is None or y is None or y == 0.0:
            stats["skipped_no_position"] += 1
            continue

        page       = doc[page_idx]
        pw         = page.rect.width
        ph         = page.rect.height
        sep_x      = pw * _SEPARATOR_X_RATIO
        ann_x      = pw * _ANNOTATION_X_RATIO

        # Adaptive font size
        eff_fs = _FONT_SIZE_DENSE if page_ann_count.get(page_idx, 0) > _DENSE_THRESHOLD else font_size

        # ── Dataset header (once per form per page) ──
        form_page_key = f"{field.form_code}_{page_idx}"
        if field.form_code and form_page_key not in form_header_written:
            form_header_written.add(form_page_key)

            fc_set    = page_form_codes.get(page_idx, set())
            page_fc   = next(iter(fc_set)) if len(fc_set) == 1 else field.form_code
            page_doms = _get_all_domains_for_page(
                page_results.get(page_idx, []), form_code=page_fc
            )
            if page_doms:
                header_bottom = _draw_dataset_headers(page, page_doms, ann_x + _TICK_LENGTH, pw)
                tracker.reserve_top(page_idx, header_bottom)

        # ── Separator line (once per page) ──
        if page_idx not in pages_with_separator:
            pages_with_separator.add(page_idx)
            _draw_separator_line(page, sep_x, _PAGE_TOP_MARGIN - 10, ph - _PAGE_BOTTOM_MARGIN)

        # ── Build annotation entries ──
        ann_entries = _build_annotations_list(result)
        if not ann_entries:
            continue

        if len(ann_entries) > 1:
            stats["multi_domain_fields"] += 1

        # ── Calculate total stack height ──
        box_h        = eff_fs + 2 * _BOX_PADDING_Y
        stack_h      = (box_h + _MULTI_BOX_SPACING) * len(ann_entries) - _MULTI_BOX_SPACING

        # ── Find non-overlapping Y slot ──
        slot_y = tracker.find_slot(page_idx, y, stack_h, ph)

        # ── Draw each box ──
        y_off    = 0.0
        any_drawn = False

        for entry in ann_entries:
            ann_text = entry["text"]
            if not ann_text:
                continue

            if tracker.is_duplicate(page_idx, ann_text):
                stats["duplicates_skipped"] += 1
                continue

            text_y = slot_y + y_off

            if entry["is_not_submitted"]:
                border_c = _NOT_SUB_BORDER
                fill_c   = _NOT_SUB_FILL
                text_c   = _NOT_SUB_TEXT
                font_n   = _FONT_NAME
                stats["not_submitted"] += 1
            else:
                border_c, fill_c = _get_domain_colours(entry["domain"])
                text_c   = _TEXT_COLOUR
                font_n   = _FONT_NAME_BOLD

            # Measure text width
            tw = fitz.get_text_length(ann_text, fontname=font_n, fontsize=eff_fs)

            box_rect = fitz.Rect(
                ann_x,
                text_y - eff_fs - _BOX_PADDING_Y,
                ann_x + tw + 2 * _BOX_PADDING_X,
                text_y + _BOX_PADDING_Y,
            )

            # Draw box
            if entry["is_not_submitted"]:
                page.draw_rect(
                    box_rect,
                    color=border_c,
                    fill=fill_c,
                    width=_BORDER_WIDTH,
                    dashes="[2 2] 0",
                    overlay=True,
                )
            else:
                page.draw_rect(
                    box_rect,
                    color=border_c,
                    fill=fill_c,
                    width=_BORDER_WIDTH,
                    overlay=True,
                )

            # Text inside box
            page.insert_text(
                fitz.Point(ann_x + _BOX_PADDING_X, text_y),
                ann_text,
                fontsize=eff_fs,
                fontname=font_n,
                color=text_c,
            )

            # Tick line from separator to box
            tick_y = text_y - eff_fs / 2
            _draw_tick(page, sep_x, ann_x, tick_y)

            y_off     += box_h + _MULTI_BOX_SPACING
            any_drawn  = True
            stats["total_annotations"] += 1

        if any_drawn:
            stats["pages_annotated"].add(page_idx)

    # ── Append legend page ──
    ref_page   = doc[0]
    ref_w      = ref_page.rect.width
    ref_h      = ref_page.rect.height
    _append_legend_page(doc, ref_w, ref_h)

    # ── Hierarchical bookmarks ──
    toc = _build_toc(form_first_page, form_primary_domain)

    # Add legend entry at the end
    legend_page_num = doc.page_count  # 1-based page number of legend
    toc.append([1, "Colour Legend", legend_page_num])

    if toc:
        try:
            doc.set_toc(toc)
        except Exception as e:
            logger.warning(f"Could not set TOC: {e}")

    # ── Save ──
    stats["pages_annotated"] = len(stats["pages_annotated"])

    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_pdf_path), deflate=True, garbage=4)
    doc.close()

    logger.info(
        "PDF annotated: %d annotations on %d pages, %d multi-domain fields, "
        "%d duplicates skipped, %d not-submitted",
        stats["total_annotations"],
        stats["pages_annotated"],
        stats["multi_domain_fields"],
        stats["duplicates_skipped"],
        stats["not_submitted"],
    )

    return stats
