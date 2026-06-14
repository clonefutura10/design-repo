"""
Professional aCRF PDF Annotation Writer — CDISC industry-standard output.

Visual features matching real annotated CRF conventions:
- Right-margin annotations as colored text with subtle boxes
- NO separator line, NO tick marks (clean professional look)
- Colour-coded by SDTM domain class (Events/Interventions/Findings/Special)
- Stacked entries for multi-domain mappings
- Domain dataset-name headers at top-left with colored background box
- [NOT SUBMITTED] in distinct grey with dashed border
- Where-clauses on second line, same font weight
- Hierarchical PDF bookmarks: domain-class → form
- Legend page appended at the end of the output PDF
- "For Annotations see page X" on repeated form pages
- Adaptive font sizing for dense pages
- Robust per-page overlap avoidance
- REPEATED ANNOTATIONS: Same variable at different Y positions is annotated
  each time (CDISC requirement for multi-instance fields)
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

_FONT_NAME = "helv"
_FONT_NAME_BOLD = "hebo"

_FONT_SIZE = 7.5
_FONT_SIZE_DENSE = 6.5
_HEADER_FONT_SIZE = 8.0
_LEGEND_FONT_SIZE = 8.0

_BORDER_WIDTH = 0.6
_BOX_PADDING_X = 3.0
_BOX_PADDING_Y = 2.0
_MULTI_BOX_SPACING = 2.5

_ANNOTATION_X_RATIO = 0.56

_PAGE_TOP_MARGIN = 52.0
_PAGE_BOTTOM_MARGIN = 28.0
_DENSE_THRESHOLD = 14

_TEXT_COLOUR = (0.05, 0.05, 0.05)
_NOT_SUB_TEXT = (0.38, 0.38, 0.38)
_NOT_SUB_BORDER = (0.55, 0.55, 0.55)
_NOT_SUB_FILL = (0.96, 0.96, 0.96)

_HEADER_BAR_HEIGHT = 11.0

_LINE_SPACING = 1.5  # extra space between primary text and where-clause


# =============================================================================
# SDTM Domain Metadata
# =============================================================================

_DOMAIN_NAMES: dict[str, str] = {
    "AE": "ADVERSE EVENTS", "BE": "BIOSPECIMEN EVENTS",
    "CE": "CLINICAL EVENTS", "CM": "CONCOMITANT MEDICATIONS",
    "CO": "COMMENTS", "DD": "DEATH DETAILS",
    "DM": "DEMOGRAPHICS", "DS": "DISPOSITION",
    "EC": "EXPOSURE AS COLLECTED", "EG": "ECG TEST RESULTS",
    "EX": "EXPOSURE", "FA": "FINDINGS ABOUT",
    "FACE": "FINDINGS ABOUT – CLINICAL EVENTS",
    "FAHO": "FINDINGS ABOUT – HEALTHCARE ENCOUNTERS",
    "HO": "HEALTHCARE ENCOUNTERS",
    "IE": "INCLUSION / EXCLUSION CRITERIA",
    "IS": "IMMUNOGENICITY SPECIMEN", "LB": "LABORATORY TEST RESULTS",
    "MB": "MICROBIOLOGY SPECIMEN", "MH": "MEDICAL HISTORY",
    "PC": "PHARMACOKINETICS CONCENTRATIONS",
    "PE": "PHYSICAL EXAMINATION", "PR": "PROCEDURES",
    "QS": "QUESTIONNAIRES", "RE": "RESPIRATORY SYSTEM FINDINGS",
    "RP": "REPRODUCTIVE SYSTEM FINDINGS", "RS": "DISEASE RESPONSE",
    "SC": "SUBJECT CHARACTERISTICS", "SU": "SUBSTANCE USE",
    "SV": "SUBJECT VISITS", "TI": "TRIAL INCLUSION / EXCLUSION",
    "TR": "TUMOR / LESION RESULTS", "TU": "TUMOR IDENTIFICATION",
    "VS": "VITAL SIGNS",
    "SUPPDM": "SUPPLEMENTAL DEMOGRAPHICS",
    "SUPPAE": "SUPPLEMENTAL ADVERSE EVENTS",
    "SUPPCM": "SUPPLEMENTAL CONCOMITANT MEDICATIONS",
    "SUPPEG": "SUPPLEMENTAL ECG TEST RESULTS",
    "SUPPFA": "SUPPLEMENTAL FINDINGS ABOUT",
    "SUPPHO": "SUPPLEMENTAL HEALTHCARE ENCOUNTERS",
    "SUPPIE": "SUPPLEMENTAL INCLUSION / EXCLUSION",
    "SUPPLB": "SUPPLEMENTAL LABORATORY TEST RESULTS",
    "SUPPMH": "SUPPLEMENTAL MEDICAL HISTORY",
    "SUPPPR": "SUPPLEMENTAL PROCEDURES",
    "SUPPSU": "SUPPLEMENTAL SUBSTANCE USE",
    "SUPPVS": "SUPPLEMENTAL VITAL SIGNS",
}

_DOMAIN_CLASSES: dict[str, list[str]] = {
    "Events": ["AE", "CE", "DD", "HO", "MH"],
    "Interventions": ["CM", "EC", "EX", "PR", "SU"],
    "Findings": ["BE", "EG", "FA", "FACE", "FAHO", "IS", "LB", "MB", "PC", "PE", "QS", "RE", "RP", "VS"],
    "Special": ["CO", "DM", "DS", "IE", "SC", "SV", "TI"],
    "Oncology": ["RS", "TR", "TU"],
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
# Domain Colour Map (border_rgb, fill_rgb)
# Fill colours are PROMINENT saturated pastels — clearly visible background
# =============================================================================

_DOMAIN_COLOURS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    # Events — warm red
    "AE": ((0.72, 0.08, 0.08), (1.00, 0.82, 0.82)),
    "CE": ((0.72, 0.08, 0.08), (1.00, 0.82, 0.82)),
    "DD": ((0.72, 0.08, 0.08), (1.00, 0.82, 0.82)),
    "HO": ((0.72, 0.08, 0.08), (1.00, 0.82, 0.82)),
    "MH": ((0.72, 0.08, 0.08), (1.00, 0.82, 0.82)),
    # Interventions — forest green
    "CM": ((0.06, 0.45, 0.06), (0.80, 0.96, 0.80)),
    "EC": ((0.06, 0.45, 0.06), (0.80, 0.96, 0.80)),
    "EX": ((0.06, 0.45, 0.06), (0.80, 0.96, 0.80)),
    "PR": ((0.06, 0.45, 0.06), (0.80, 0.96, 0.80)),
    "SU": ((0.06, 0.45, 0.06), (0.80, 0.96, 0.80)),
    # Findings — royal blue
    "BE": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    "EG": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    "FA": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    "FACE": ((0.00, 0.44, 0.52), (0.78, 0.95, 1.00)),
    "FAHO": ((0.00, 0.44, 0.52), (0.78, 0.95, 1.00)),
    "IS": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    "LB": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    "MB": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    "PC": ((0.00, 0.40, 0.40), (0.78, 0.96, 0.96)),
    "PE": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    "QS": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    "RE": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    "RP": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    "VS": ((0.06, 0.12, 0.68), (0.82, 0.86, 1.00)),
    # Special Purpose — medium purple
    "CO": ((0.42, 0.06, 0.58), (0.90, 0.82, 1.00)),
    "DM": ((0.42, 0.06, 0.58), (0.90, 0.82, 1.00)),
    "DS": ((0.42, 0.06, 0.58), (0.90, 0.82, 1.00)),
    "IE": ((0.42, 0.06, 0.58), (0.90, 0.82, 1.00)),
    "SC": ((0.42, 0.06, 0.58), (0.90, 0.82, 1.00)),
    "SV": ((0.42, 0.06, 0.58), (0.90, 0.82, 1.00)),
    "TI": ((0.42, 0.06, 0.58), (0.90, 0.82, 1.00)),
    # Oncology — dark maroon
    "RS": ((0.52, 0.00, 0.18), (1.00, 0.80, 0.86)),
    "TR": ((0.52, 0.00, 0.18), (1.00, 0.80, 0.86)),
    "TU": ((0.52, 0.00, 0.18), (1.00, 0.80, 0.86)),
}

_DEFAULT_BORDER = (0.35, 0.35, 0.35)
_DEFAULT_FILL = (0.96, 0.96, 0.96)


def _get_domain_colours(domain: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    d = domain.upper()
    if d in _DOMAIN_COLOURS:
        return _DOMAIN_COLOURS[d]
    if d.startswith("SUPP"):
        base = d[4:]
        if base in _DOMAIN_COLOURS:
            return _DOMAIN_COLOURS[base]
    return (_DEFAULT_BORDER, _DEFAULT_FILL)


# =============================================================================
# Annotation Entry Builder
# =============================================================================

def _build_annotations_list(result: ResolutionResult) -> list[dict]:
    """
    Return a list of annotation dicts for a field.

    Same-domain variables are combined on one line with ' / ' separator.
    Different domains stay as separate stacked entries.
    """
    annotations: list[dict] = []

    if result.is_not_submitted:
        annotations.append({
            "text": "[NOT SUBMITTED]",
            "domain": "",
            "is_not_submitted": True,
            "is_supp": False,
            "where_clause": "",
            "is_derived": getattr(result, "is_derived", False),
        })
        return annotations

    if not result.sdtm_domain or not result.sdtm_variable:
        return annotations

    domain = result.sdtm_domain.upper()
    is_supp = result.is_supplemental
    if is_supp:
        prefix = domain if domain.startswith("SUPP") else f"SUPP{domain}"
        primary_text = f"{prefix}.{result.sdtm_variable}"
    else:
        primary_text = f"{domain}.{result.sdtm_variable}"

    if result.codelist_code:
        primary_text += f" ({result.codelist_code})"

    base_domain = domain[4:] if domain.startswith("SUPP") else domain

    from collections import OrderedDict
    groups: OrderedDict[tuple[str, bool], list[str]] = OrderedDict()
    groups[(base_domain, is_supp)] = [primary_text]

    for mapping in getattr(result, "additional_mappings", None) or []:
        add_domain = (mapping.get("domain") or mapping.get("sdtm_domain", "")).upper()
        add_variable = mapping.get("variable") or mapping.get("sdtm_variable", "")
        add_codelist = mapping.get("codelist") or mapping.get("codelist_code", "")
        add_is_supp = mapping.get("is_supp", False) or mapping.get("is_supplemental", False)

        if not add_domain or not add_variable:
            continue

        if add_is_supp:
            pfx = add_domain if add_domain.startswith("SUPP") else f"SUPP{add_domain}"
            add_text = f"{pfx}.{add_variable}"
        else:
            add_text = f"{add_domain}.{add_variable}"

        if add_codelist:
            add_text += f" ({add_codelist})"

        ann_base = add_domain[4:] if add_domain.startswith("SUPP") else add_domain
        key = (ann_base, add_is_supp)
        if key not in groups:
            groups[key] = []
        groups[key].append(add_text)

    where_clause = getattr(result, "where_clause", "") or ""
    is_derived = getattr(result, "is_derived", False)

    for (grp_domain, grp_is_supp), texts in groups.items():
        combined_text = " / ".join(texts)
        annotations.append({
            "text": combined_text,
            "domain": grp_domain,
            "is_not_submitted": False,
            "is_supp": grp_is_supp,
            "where_clause": where_clause if grp_domain == base_domain else "",
            "is_derived": is_derived,
        })

    return annotations


# =============================================================================
# Page Domain Helpers
# =============================================================================

def _get_all_domains_for_page(results_on_page: list[ResolutionResult], form_code: str = "") -> list[str]:
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
# Domain Header — top-LEFT of page, WITH colored background box
# =============================================================================

_DOMAIN_HEADER_LEFT_X = 36.0
_DOMAIN_HEADER_Y = 62.0


def _draw_domain_name_top_left(page: fitz.Page, domains: list[str]) -> None:
    """
    Draw coloured domain-name boxes at the top-left of the page.
    Format: 'VS = VITAL SIGNS' inside a colored background box.
    """
    if not domains:
        return

    y = _DOMAIN_HEADER_Y
    for domain in domains:
        full_name = _get_domain_full_name(domain)
        label_text = f"{domain} = {full_name}"
        border_c, fill_c = _get_domain_colours(domain)

        bar_fill = (
            max(0.0, fill_c[0] - 0.06),
            max(0.0, fill_c[1] - 0.06),
            max(0.0, fill_c[2] - 0.06),
        )

        tw = fitz.get_text_length(label_text, fontname=_FONT_NAME_BOLD, fontsize=_HEADER_FONT_SIZE)
        box_rect = fitz.Rect(
            _DOMAIN_HEADER_LEFT_X - 2,
            y - _HEADER_BAR_HEIGHT + 1,
            _DOMAIN_HEADER_LEFT_X + tw + 6,
            y + 2,
        )
        page.draw_rect(box_rect, color=border_c, fill=bar_fill, width=0.9, overlay=True)
        page.insert_text(
            fitz.Point(_DOMAIN_HEADER_LEFT_X, y),
            label_text,
            fontsize=_HEADER_FONT_SIZE,
            fontname=_FONT_NAME_BOLD,
            color=border_c,
        )
        y += _HEADER_BAR_HEIGHT + 2.0


def _draw_see_page_reference(page: fitz.Page, first_pages: list[int], page_height: float, ann_x: float) -> None:
    """Write 'For Annotations see page X · Y' at the bottom of annotation column."""
    if not first_pages:
        return

    page_nums = [str(p + 1) for p in first_pages]
    if len(page_nums) <= 3:
        ref_text = f"For Annotations see page {' · '.join(page_nums)}"
    else:
        ref_text = f"For Annotations see page {page_nums[0]} – {page_nums[-1]}"

    y = page_height - _PAGE_BOTTOM_MARGIN - 4.0
    page.insert_text(
        fitz.Point(ann_x, y),
        ref_text,
        fontsize=7.0,
        fontname=_FONT_NAME,
        color=(0.35, 0.35, 0.35),
    )


# =============================================================================
# Overlap Tracker — POSITION-AWARE duplicate detection
# =============================================================================

_DEDUP_Y_TOLERANCE = 3.0


class _OverlapTracker:
    """
    Tracks placed annotation spans per page.

    Duplicate detection is POSITION-AWARE: the same annotation text
    IS allowed at different Y positions (required for repeated fields
    like "Result" in Vital Signs). Only exact same text at the same
    Y position is considered a duplicate.
    """

    def __init__(self):
        self._occupied: dict[int, list[tuple[float, float]]] = defaultdict(list)
        self._placed: dict[int, list[tuple[str, float]]] = defaultdict(list)

    def is_duplicate(self, page_idx: int, text: str, y_pos: float) -> bool:
        for placed_text, placed_y in self._placed[page_idx]:
            if placed_text == text and abs(placed_y - y_pos) < _DEDUP_Y_TOLERANCE:
                return True
        self._placed[page_idx].append((text, y_pos))
        return False

    def find_slot(self, page_idx: int, desired_y: float, box_height: float, page_height: float) -> float:
        min_y = _PAGE_TOP_MARGIN + box_height
        max_y = page_height - _PAGE_BOTTOM_MARGIN
        gap = 1.5

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

        for direction in (1, -1):
            y = desired_y
            for _ in range(80):
                y += direction * (box_height + gap)
                if y > max_y or y < min_y:
                    break
                if not _overlaps(y):
                    self._mark(page_idx, y, box_height, gap)
                    return y

        fallback = max_y - len(self._occupied[page_idx]) * (box_height + gap)
        self._mark(page_idx, max(min_y, fallback), box_height, gap)
        return max(min_y, fallback)

    def _mark(self, page_idx: int, y: float, h: float, gap: float):
        self._occupied[page_idx].append((y - h - gap, y + gap))


# =============================================================================
# Legend Page
# =============================================================================

def _append_legend_page(doc: fitz.Document, page_width: float, page_height: float):
    """Append a colour-legend page at the end of the PDF."""
    page = doc.new_page(width=page_width, height=page_height)

    page.insert_text(fitz.Point(36, 44), "SDTM Annotation Colour Legend",
                     fontsize=14, fontname=_FONT_NAME_BOLD, color=(0.10, 0.10, 0.10))
    page.insert_text(fitz.Point(36, 56),
                     "Colour coding applied to annotated CRF (aCRF) variable annotations by SDTM domain class",
                     fontsize=8, fontname=_FONT_NAME, color=(0.40, 0.40, 0.40))
    page.draw_line(fitz.Point(36, 60), fitz.Point(page_width - 36, 60),
                   color=(0.70, 0.70, 0.70), width=0.5)

    col_x = [38.0, 310.0]
    row_height = 16.0
    box_w, box_h = 18.0, 10.0
    y, col = 76.0, 0
    class_label_written: set[str] = set()

    for cls_name, members in _DOMAIN_CLASSES.items():
        if cls_name not in class_label_written:
            x = col_x[col]
            page.insert_text(fitz.Point(x, y), cls_name.upper(),
                             fontsize=8, fontname=_FONT_NAME_BOLD, color=(0.20, 0.20, 0.20))
            y += row_height * 0.7
            class_label_written.add(cls_name)

        for domain in members:
            if y > page_height - 50:
                col = min(col + 1, len(col_x) - 1)
                y = 76.0
            x = col_x[col]
            border_c, fill_c = _get_domain_colours(domain)
            swatch_rect = fitz.Rect(x, y - box_h + 1, x + box_w, y + 1)
            page.draw_rect(swatch_rect, color=border_c, fill=fill_c, width=0.7)
            full = _get_domain_full_name(domain)
            page.insert_text(fitz.Point(x + box_w + 5, y), f"{domain}  —  {full}",
                             fontsize=7.5, fontname=_FONT_NAME, color=(0.10, 0.10, 0.10))
            y += row_height
        y += row_height * 0.5

    x = col_x[col]
    if y > page_height - 50:
        col = min(col + 1, len(col_x) - 1)
        y = 76.0
        x = col_x[col]
    page.insert_text(fitz.Point(x, y), "OTHER", fontsize=8, fontname=_FONT_NAME_BOLD,
                     color=(0.20, 0.20, 0.20))
    y += row_height * 0.7
    ns_rect = fitz.Rect(x, y - box_h + 1, x + box_w, y + 1)
    page.draw_rect(ns_rect, color=_NOT_SUB_BORDER, fill=_NOT_SUB_FILL, width=0.7, dashes="[2 2] 0")
    page.insert_text(fitz.Point(x + box_w + 5, y),
                     "[NOT SUBMITTED]  —  Field not collected / derived / internal",
                     fontsize=7.5, fontname=_FONT_NAME, color=(0.10, 0.10, 0.10))

    page.draw_line(fitz.Point(36, page_height - 42), fitz.Point(page_width - 36, page_height - 42),
                   color=(0.75, 0.75, 0.75), width=0.4)
    page.insert_text(fitz.Point(36, page_height - 32),
                     "Supplemental Qualifier variables are annotated with the SUPP-prefixed dataset name (e.g. SUPPVS.QVAL).",
                     fontsize=7, fontname=_FONT_NAME, color=(0.45, 0.45, 0.45))
    page.insert_text(fitz.Point(36, page_height - 22),
                     "This aCRF was generated automatically. Verify all annotations against the study SDTM specification.",
                     fontsize=7, fontname=_FONT_NAME, color=(0.45, 0.45, 0.45))


# =============================================================================
# Hierarchical Bookmark Builder
# =============================================================================

def _build_toc(form_first_page: dict[str, tuple[int, str]], form_domains: dict[str, str]) -> list[list]:
    class_domain_forms: dict[str, dict[str, list[tuple[str, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for form_code, (page_idx, _) in sorted(form_first_page.items(), key=lambda x: x[1][0]):
        domain = form_domains.get(form_code, "")
        cls = _domain_class(domain) if domain else "Other"
        class_domain_forms[cls][domain or "??"].append((form_code, page_idx))

    toc: list[list] = []
    class_order = list(_DOMAIN_CLASSES.keys()) + ["Other"]
    for cls in class_order:
        if cls not in class_domain_forms:
            continue
        toc.append([1, cls, list(class_domain_forms[cls].values())[0][0][1] + 1])
        for domain, forms in sorted(class_domain_forms[cls].items(), key=lambda x: x[0]):
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
    """
    if len(results) != len(fields):
        raise ValueError(f"results ({len(results)}) and fields ({len(fields)}) length mismatch")

    doc = fitz.open(str(input_pdf_path))

    stats: dict = {
        "total_annotations": 0,
        "pages_annotated": set(),
        "not_submitted": 0,
        "skipped_no_position": 0,
        "duplicates_skipped": 0,
        "multi_domain_fields": 0,
    }

    tracker = _OverlapTracker()

    # Pre-group by page
    page_results: dict[int, list[ResolutionResult]] = defaultdict(list)
    page_form_codes: dict[int, set[str]] = defaultdict(set)
    form_all_pages: dict[str, list[int]] = defaultdict(list)

    for field, result in zip(fields, results):
        pi = field.page_index
        if pi is not None:
            page_results[pi].append(result)
            if field.form_code:
                page_form_codes[pi].add(field.form_code)
                if pi not in form_all_pages[field.form_code]:
                    form_all_pages[field.form_code].append(pi)

    # Identify first instance pages per form_code
    _INSTANCE_GAP = 3
    form_first_instance_pages: dict[str, set[int]] = {}
    for fc, pages in form_all_pages.items():
        pages_sorted = sorted(set(pages))
        first: list[int] = []
        for p in pages_sorted:
            if not first or p - first[-1] <= _INSTANCE_GAP:
                first.append(p)
            else:
                break
        form_first_instance_pages[fc] = set(first)

    form_see_pages: dict[str, set[int]] = {
        fc: set(ps for ps in pages if ps not in form_first_instance_pages[fc])
        for fc, pages in form_all_pages.items()
    }

    page_ann_count: dict[int, int] = defaultdict(int)
    for field, result in zip(fields, results):
        pi = field.page_index
        if pi is None:
            continue
        fc = field.form_code or ""
        if fc and pi in form_see_pages.get(fc, set()):
            continue
        if result.resolved or result.is_not_submitted:
            page_ann_count[pi] += 1

    form_first_page: dict[str, tuple[int, str]] = {}
    form_primary_domain: dict[str, str] = {}
    for field, result in zip(fields, results):
        fc = field.form_code
        if fc and fc not in form_first_page and field.page_index is not None:
            form_first_page[fc] = (field.page_index, fc)
        if fc and fc not in form_primary_domain and result.sdtm_domain:
            d = result.sdtm_domain.upper()
            form_primary_domain[fc] = d[4:] if d.startswith("SUPP") else d

    domain_header_written: set[int] = set()
    see_page_written: set[str] = set()

    for field, result in zip(fields, results):
        if not result.resolved and not result.is_not_submitted:
            continue

        page_idx = field.page_index
        y = field.y
        fc = field.form_code or ""

        if page_idx is None or y is None or y == 0.0:
            stats["skipped_no_position"] += 1
            continue

        page = doc[page_idx]
        pw = page.rect.width
        ph = page.rect.height
        ann_x = pw * _ANNOTATION_X_RATIO

        # Domain name at top-LEFT with colored box (once per page)
        if page_idx not in domain_header_written:
            domain_header_written.add(page_idx)
            fc_set = page_form_codes.get(page_idx, set())
            page_fc = next(iter(fc_set)) if len(fc_set) == 1 else fc
            page_doms = _get_all_domains_for_page(
                page_results.get(page_idx, []), form_code=page_fc
            )
            if page_doms:
                _draw_domain_name_top_left(page, page_doms)

        # "For Annotations see page X" for repeat-visit pages
        if fc and page_idx in form_see_pages.get(fc, set()):
            see_key = f"{fc}_{page_idx}"
            if see_key not in see_page_written:
                see_page_written.add(see_key)
                first_inst = sorted(form_first_instance_pages.get(fc, []))
                _draw_see_page_reference(page, first_inst, ph, ann_x)
            continue

        eff_fs = _FONT_SIZE_DENSE if page_ann_count.get(page_idx, 0) > _DENSE_THRESHOLD else font_size

        ann_entries = _build_annotations_list(result)
        if not ann_entries:
            continue

        if len(ann_entries) > 1:
            stats["multi_domain_fields"] += 1

        def _entry_height(entry: dict) -> float:
            h = eff_fs + 2 * _BOX_PADDING_Y
            if entry.get("where_clause"):
                h += eff_fs + _LINE_SPACING
            return h

        stack_h = sum(_entry_height(e) + _MULTI_BOX_SPACING for e in ann_entries) - _MULTI_BOX_SPACING
        slot_y = tracker.find_slot(page_idx, y, stack_h, ph)

        y_off = 0.0
        any_drawn = False

        for entry in ann_entries:
            ann_text = entry["text"]
            where_clause = entry.get("where_clause", "") or ""
            is_derived = entry.get("is_derived", False)

            if not ann_text:
                continue

            if tracker.is_duplicate(page_idx, ann_text + where_clause, slot_y + y_off):
                stats["duplicates_skipped"] += 1
                continue

            text_y = slot_y + y_off
            this_h = _entry_height(entry)

            if entry["is_not_submitted"]:
                border_c = _NOT_SUB_BORDER
                fill_c = _NOT_SUB_FILL
                text_c = _NOT_SUB_TEXT
                font_n = _FONT_NAME
                use_dash = True
                stats["not_submitted"] += 1
            else:
                border_c, fill_c = _get_domain_colours(entry["domain"])
                text_c = border_c
                font_n = _FONT_NAME_BOLD
                use_dash = is_derived

            tw = fitz.get_text_length(ann_text, fontname=font_n, fontsize=eff_fs)
            if where_clause:
                wc_text = f"where {where_clause}"
                tw_wc = fitz.get_text_length(wc_text, fontname=_FONT_NAME, fontsize=eff_fs)
                tw = max(tw, tw_wc)

            box_rect = fitz.Rect(
                ann_x,
                text_y - eff_fs - _BOX_PADDING_Y,
                ann_x + tw + 2 * _BOX_PADDING_X,
                text_y + _BOX_PADDING_Y + ((eff_fs + _LINE_SPACING) if where_clause else 0),
            )

            if use_dash:
                page.draw_rect(box_rect, color=border_c, fill=fill_c,
                               width=_BORDER_WIDTH, dashes="[2 2] 0", overlay=True)
            else:
                page.draw_rect(box_rect, color=border_c, fill=fill_c,
                               width=_BORDER_WIDTH, overlay=True)

            page.insert_text(fitz.Point(ann_x + _BOX_PADDING_X, text_y), ann_text,
                             fontsize=eff_fs, fontname=font_n, color=text_c)

            if where_clause:
                wc_y = text_y + eff_fs + _LINE_SPACING
                wc_colour = (
                    min(1.0, text_c[0] * 0.7 + 0.15),
                    min(1.0, text_c[1] * 0.7 + 0.15),
                    min(1.0, text_c[2] * 0.7 + 0.15),
                )
                page.insert_text(fitz.Point(ann_x + _BOX_PADDING_X, wc_y), wc_text,
                                 fontsize=eff_fs, fontname=_FONT_NAME, color=wc_colour)

            y_off += this_h + _MULTI_BOX_SPACING
            any_drawn = True
            stats["total_annotations"] += 1

        if any_drawn:
            stats["pages_annotated"].add(page_idx)

    ref_page = doc[0]
    _append_legend_page(doc, ref_page.rect.width, ref_page.rect.height)

    toc = _build_toc(form_first_page, form_primary_domain)
    toc.append([1, "Colour Legend", doc.page_count])
    if toc:
        try:
            doc.set_toc(toc)
        except Exception as e:
            logger.warning(f"Could not set TOC: {e}")

    stats["pages_annotated"] = len(stats["pages_annotated"])
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_pdf_path), deflate=True, garbage=4)
    doc.close()

    logger.info(
        "PDF annotated: %d annotations on %d pages, %d multi-domain, "
        "%d duplicates skipped, %d not-submitted, %d skipped (no position)",
        stats["total_annotations"], stats["pages_annotated"],
        stats["multi_domain_fields"], stats["duplicates_skipped"],
        stats["not_submitted"], stats["skipped_no_position"],
    )

    return stats
