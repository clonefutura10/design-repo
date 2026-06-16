"""
Professional aCRF PDF Annotation Writer — CDISC industry-standard output.

Visual features matching real annotated CRF conventions:
- Right-margin annotations as colored text with subtle boxes
- NO separator line, NO tick marks (clean professional look)
- Colour-coded by SDTM domain (unique colour per domain)
- Stacked entries for multi-domain mappings placed SIDE BY SIDE
- Domain dataset-name headers at top-left with colored background box
- [NOT SUBMITTED] in distinct grey with dashed border
- Where-clauses on second line, same font weight
- Hierarchical PDF bookmarks: domain-class → form
- Legend page appended at the end of the output PDF
- "For Annotations see page X" on repeated form pages
- Adaptive font sizing for dense pages
- Horizontal stagger for overlapping annotations (different fields)
- Side-by-side layout for multiple mappings (same field)
- REPEATED ANNOTATIONS: Same variable at different Y positions is annotated
  each time (CDISC requirement for multi-instance fields)
"""

from __future__ import annotations

from pathlib import Path
from collections import defaultdict

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
_MULTI_BOX_H_GAP = 4.0  # horizontal gap between side-by-side boxes

_ANNOTATION_X_RATIO = 0.56

_PAGE_TOP_MARGIN = 52.0
_PAGE_BOTTOM_MARGIN = 28.0
_DENSE_THRESHOLD = 14

_TEXT_COLOUR = (0.05, 0.05, 0.05)
_NOT_SUB_TEXT = (0.38, 0.38, 0.38)
_NOT_SUB_BORDER = (0.55, 0.55, 0.55)
_NOT_SUB_FILL = (0.96, 0.96, 0.96)

_HEADER_BAR_HEIGHT = 11.0

_LINE_SPACING = 1.5

_FT_LINE_FACTOR = 1.45
_FT_SIDE_INSET = 4.0

# Horizontal stagger for overlapping annotations (different fields)
_STAGGER_SHIFT_X = -120.0

# Navigation-note style
_NOTE_FONT_SIZE = 9.0
_NOTE_FILL = (0.93, 0.95, 0.99)
_NOTE_BORDER = (0.30, 0.42, 0.62)
_NOTE_TEXT = (0.18, 0.28, 0.48)


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
# Domain Colour Map — UNIQUE colour per domain (border_rgb, fill_rgb)
# =============================================================================

_DOMAIN_COLOURS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "AE": ((0.80, 0.05, 0.05), (1.00, 0.85, 0.85)),
    "CE": ((0.75, 0.35, 0.00), (1.00, 0.90, 0.78)),
    "DD": ((0.50, 0.00, 0.00), (0.92, 0.78, 0.78)),
    "HO": ((0.85, 0.20, 0.40), (1.00, 0.84, 0.88)),
    "MH": ((0.60, 0.00, 0.30), (0.94, 0.80, 0.86)),
    "CM": ((0.00, 0.52, 0.00), (0.82, 0.96, 0.82)),
    "EC": ((0.00, 0.50, 0.45), (0.78, 0.95, 0.93)),
    "EX": ((0.35, 0.55, 0.00), (0.88, 0.94, 0.76)),
    "PR": ((0.10, 0.38, 0.10), (0.80, 0.92, 0.80)),
    "SU": ((0.00, 0.42, 0.30), (0.78, 0.93, 0.88)),
    "BE": ((0.00, 0.25, 0.70), (0.80, 0.86, 1.00)),
    "EG": ((0.45, 0.00, 0.70), (0.90, 0.80, 1.00)),
    "FA": ((0.00, 0.40, 0.60), (0.78, 0.90, 0.96)),
    "FACE": ((0.00, 0.55, 0.55), (0.78, 0.94, 0.94)),
    "FAHO": ((0.20, 0.30, 0.60), (0.82, 0.85, 0.96)),
    "IS": ((0.30, 0.10, 0.55), (0.86, 0.80, 0.94)),
    "LB": ((0.00, 0.30, 0.55), (0.78, 0.87, 0.96)),
    "MB": ((0.10, 0.45, 0.55), (0.80, 0.91, 0.94)),
    "PC": ((0.00, 0.50, 0.50), (0.78, 0.94, 0.94)),
    "PE": ((0.25, 0.25, 0.65), (0.84, 0.84, 0.98)),
    "QS": ((0.50, 0.20, 0.50), (0.92, 0.82, 0.92)),
    "RE": ((0.00, 0.35, 0.70), (0.78, 0.88, 1.00)),
    "RP": ((0.55, 0.00, 0.55), (0.93, 0.78, 0.93)),
    "VS": ((0.00, 0.45, 0.75), (0.78, 0.90, 1.00)),
    "CO": ((0.40, 0.40, 0.00), (0.92, 0.92, 0.76)),
    "DM": ((0.55, 0.00, 0.68), (0.92, 0.78, 0.96)),
    "DS": ((0.00, 0.45, 0.20), (0.78, 0.93, 0.84)),
    "IE": ((0.60, 0.30, 0.00), (0.96, 0.88, 0.76)),
    "SC": ((0.50, 0.10, 0.40), (0.92, 0.80, 0.90)),
    "SV": ((0.20, 0.20, 0.55), (0.84, 0.84, 0.94)),
    "TI": ((0.45, 0.25, 0.00), (0.92, 0.86, 0.76)),
    "RS": ((0.65, 0.00, 0.20), (0.96, 0.78, 0.84)),
    "TR": ((0.70, 0.25, 0.00), (0.98, 0.86, 0.76)),
    "TU": ((0.55, 0.00, 0.40), (0.94, 0.78, 0.88)),
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
    base_domain = domain[4:] if domain.startswith("SUPP") else domain

    if is_supp:
        prefix = domain if domain.startswith("SUPP") else f"SUPP{domain}"
        primary_text = f"{prefix}.QVAL"
        primary_qnam = result.sdtm_variable
    else:
        primary_text = f"{domain}.{result.sdtm_variable}"
        primary_qnam = ""

    if result.codelist_code and not is_supp:
        primary_text += f" ({result.codelist_code})"

    from collections import OrderedDict
    groups: OrderedDict[tuple[str, bool, str], list[str]] = OrderedDict()
    groups[(base_domain, is_supp, primary_qnam)] = [primary_text]

    for mapping in getattr(result, "additional_mappings", None) or []:
        add_domain = (mapping.get("domain") or mapping.get("sdtm_domain", "")).upper()
        add_variable = mapping.get("variable") or mapping.get("sdtm_variable", "")
        add_codelist = mapping.get("codelist") or mapping.get("codelist_code", "")
        add_is_supp = mapping.get("is_supp", False) or mapping.get("is_supplemental", False)

        if not add_domain or not add_variable:
            continue

        if add_is_supp:
            pfx = add_domain if add_domain.startswith("SUPP") else f"SUPP{add_domain}"
            add_text = f"{pfx}.QVAL"
            add_qnam = add_variable
        else:
            add_text = f"{add_domain}.{add_variable}"
            add_qnam = ""

        if add_codelist and not add_is_supp:
            add_text += f" ({add_codelist})"

        ann_base = add_domain[4:] if add_domain.startswith("SUPP") else add_domain
        key = (ann_base, add_is_supp, add_qnam)
        if key not in groups:
            groups[key] = []
        groups[key].append(add_text)

    testcd_where = getattr(result, "where_clause", "") or ""
    is_derived = getattr(result, "is_derived", False)
    value_decode = getattr(result, "value_decode", "") or ""

    for grp_index, ((grp_domain, grp_is_supp, grp_qnam), texts) in enumerate(groups.items()):
        combined_text = " / ".join(texts)
        if grp_is_supp:
            wc = f"QNAM = {grp_qnam}" if grp_qnam else ""
        elif grp_domain == base_domain and not is_supp:
            wc = testcd_where
        else:
            wc = ""

        annotations.append({
            "text": combined_text,
            "domain": grp_domain,
            "is_not_submitted": False,
            "is_supp": grp_is_supp,
            "where_clause": wc,
            "is_derived": is_derived,
            "value_decode": value_decode if grp_index == 0 else "",
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


def _freetext(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    fontsize: float,
    fontname: str,
    text_color: tuple,
    fill_color: tuple,
    border_color: tuple,
    border_width: float = _BORDER_WIDTH,
    dashed: bool = False,
) -> None:
    """
    Create a real PDF FreeText annotation (selectable, extractable),
    rendered exactly once.
    """
    try:
        annot = page.add_freetext_annot(
            rect,
            text,
            fontsize=fontsize,
            fontname=fontname,
            text_color=text_color,
            fill_color=fill_color,
            align=fitz.TEXT_ALIGN_LEFT,
        )
    except Exception:
        page.draw_rect(rect, color=text_color, fill=fill_color, width=border_width,
                       dashes="[2 2] 0" if dashed else None, overlay=True)
        page.insert_text(fitz.Point(rect.x0 + _BOX_PADDING_X, rect.y1 - _BOX_PADDING_Y),
                         text, fontsize=fontsize, fontname=fontname, color=text_color)
        return

    try:
        if dashed:
            annot.set_border(width=border_width, dashes=[2, 2])
        else:
            annot.set_border(width=border_width)
    except Exception:
        pass
    try:
        annot.update()
    except Exception:
        pass


def _draw_domain_name_top_left(page: fitz.Page, domains: list[str]) -> None:
    """Draw coloured domain-name boxes at the top-left of the page."""
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
        _freetext(
            page, box_rect, label_text,
            fontsize=_HEADER_FONT_SIZE, fontname=_FONT_NAME_BOLD,
            text_color=border_c, fill_color=bar_fill, border_color=border_c,
            border_width=0.9,
        )
        y += _HEADER_BAR_HEIGHT + 2.0


def _draw_note(page: fitz.Page, text: str, ann_x: float, y_top: float) -> None:
    """Draw a navigation-note FreeText box."""
    tw = fitz.get_text_length(text, fontname=_FONT_NAME_BOLD, fontsize=_NOTE_FONT_SIZE)
    h = _NOTE_FONT_SIZE * _FT_LINE_FACTOR + 2 * _BOX_PADDING_Y
    rect = fitz.Rect(ann_x, y_top, ann_x + tw + 2 * _BOX_PADDING_X + _FT_SIDE_INSET, y_top + h)
    _freetext(page, rect, text, fontsize=_NOTE_FONT_SIZE, fontname=_FONT_NAME_BOLD,
              text_color=_NOTE_TEXT, fill_color=_NOTE_FILL, border_color=_NOTE_BORDER,
              border_width=0.7)


def _draw_see_page_reference(page: fitz.Page, first_pages: list[int], page_height: float, ann_x: float) -> None:
    """Write 'For Annotations see page X' as a navigation note at the bottom."""
    if not first_pages:
        return

    page_nums = [str(p + 1) for p in first_pages]
    if len(page_nums) <= 3:
        ref_text = f"For Annotations see page {' · '.join(page_nums)}"
    else:
        ref_text = f"For Annotations see page {page_nums[0]} – {page_nums[-1]}"

    h = _NOTE_FONT_SIZE * _FT_LINE_FACTOR + 2 * _BOX_PADDING_Y
    _draw_note(page, ref_text, ann_x, page_height - _PAGE_BOTTOM_MARGIN - h)


def _draw_continued_from_page(page: fitz.Page, prev_page_num: int, ann_x: float) -> None:
    """Write 'Continued from page X' near the top of the column."""
    _draw_note(page, f"Continued from page {prev_page_num}", ann_x, 72.0)


# =============================================================================
# Placement Tracker — Dedup + Horizontal Stagger on Overlap
# =============================================================================

_DEDUP_Y_TOLERANCE = 3.0


class _PlacementTracker:
    """
    Tracks placed annotations for:
    1. Deduplication (same text at same Y = skip)
    2. Horizontal stagger (when different-field annotations overlap vertically)
    """

    def __init__(self):
        self._placed: dict[int, list[tuple[str, float]]] = defaultdict(list)
        self._occupied: dict[int, list[tuple[float, float]]] = defaultdict(list)

    def is_duplicate(self, page_idx: int, text: str, y_pos: float) -> bool:
        """Check if this exact text was already placed at this Y position."""
        for placed_text, placed_y in self._placed[page_idx]:
            if placed_text == text and abs(placed_y - y_pos) < _DEDUP_Y_TOLERANCE:
                return True
        self._placed[page_idx].append((text, y_pos))
        return False

    def get_x_offset(self, page_idx: int, box_top: float, box_bottom: float) -> float:
        """
        If this box's vertical range overlaps with an existing annotation
        from a DIFFERENT field, return a LEFT shift so both are readable.
        """
        for (ot, ob) in self._occupied[page_idx]:
            if box_top < ob and box_bottom > ot:
                return _STAGGER_SHIFT_X
        return 0.0

    def mark_occupied(self, page_idx: int, box_top: float, box_bottom: float) -> None:
        """Record the vertical span of a placed annotation."""
        self._occupied[page_idx].append((box_top, box_bottom))


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

def _build_toc(
    form_first_page: dict[str, tuple[int, str]],
    form_domains: dict[str, str],
    form_visits: dict[str, list[tuple[str, int]]] | None = None,
) -> list[list]:
    """Build a dual bookmark hierarchy."""
    toc: list[list] = []

    # Branch 1: By Topic
    class_domain_forms: dict[str, dict[str, list[tuple[str, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for form_code, (page_idx, _) in sorted(form_first_page.items(), key=lambda x: x[1][0]):
        domain = form_domains.get(form_code, "")
        cls = _domain_class(domain) if domain else "Other"
        class_domain_forms[cls][domain or "??"].append((form_code, page_idx))

    topic_first_page = min(
        (pg for forms in class_domain_forms.values() for fl in forms.values() for _, pg in fl),
        default=0,
    )
    toc.append([1, "CRFs by Topic", topic_first_page + 1])
    class_order = list(_DOMAIN_CLASSES.keys()) + ["Other"]
    for cls in class_order:
        if cls not in class_domain_forms:
            continue
        toc.append([2, cls, list(class_domain_forms[cls].values())[0][0][1] + 1])
        for domain, forms in sorted(class_domain_forms[cls].items(), key=lambda x: x[0]):
            full = _get_domain_full_name(domain)
            toc.append([3, f"{domain} — {full}", forms[0][1] + 1])
            for form_code, pg in forms:
                toc.append([4, form_code, pg + 1])

    # Branch 2: By Visit
    if form_visits:
        visit_entries: dict[str, list[tuple[str, int]]] = defaultdict(list)
        visit_first_page: dict[str, int] = {}
        for form_code, occurrences in form_visits.items():
            for visit, page_idx in occurrences:
                if not visit:
                    continue
                visit_entries[visit].append((form_code, page_idx))
                if visit not in visit_first_page or page_idx < visit_first_page[visit]:
                    visit_first_page[visit] = page_idx

        if visit_entries:
            branch_first = min(visit_first_page.values())
            toc.append([1, "CRFs by Visit", branch_first + 1])
            for visit in sorted(visit_first_page, key=lambda v: visit_first_page[v]):
                forms = sorted(visit_entries[visit], key=lambda x: x[1])
                toc.append([2, visit, visit_first_page[visit] + 1])
                seen_forms: set[str] = set()
                for form_code, pg in forms:
                    if form_code in seen_forms:
                        continue
                    seen_forms.add(form_code)
                    toc.append([3, form_code, pg + 1])

    return toc


# =============================================================================
# Main annotate_pdf function
# =============================================================================

def _form_identity(field: CRFField) -> str:
    """
    Identity used to group repeat instances of the SAME form across visits.
    Uses form_code + form_name to avoid conflating distinct forms.
    """
    fc = (field.form_code or "").strip().upper()
    fn = (getattr(field, "form_name", "") or "").strip().lower()
    return f"{fc}|{fn}" if (fc or fn) else ""


def annotate_pdf(
    input_pdf_path: Path,
    output_pdf_path: Path,
    results: list[ResolutionResult],
    fields: list[CRFField],
    font_size: float = _FONT_SIZE,
) -> dict:
    """Write industry-standard aCRF annotations onto a blank CRF PDF."""
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

    tracker = _PlacementTracker()

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
            fid = _form_identity(field)
            if fid and pi not in form_all_pages[fid]:
                form_all_pages[fid].append(pi)

    # Identify first instance pages per form identity
    _INSTANCE_GAP = 3
    form_first_instance_pages: dict[str, set[int]] = {}
    for fid, pages in form_all_pages.items():
        pages_sorted = sorted(set(pages))
        first: list[int] = []
        for p in pages_sorted:
            if not first or p - first[-1] <= _INSTANCE_GAP:
                first.append(p)
            else:
                break
        form_first_instance_pages[fid] = set(first)

    form_see_pages: dict[str, set[int]] = {
        fid: set(ps for ps in pages if ps not in form_first_instance_pages[fid])
        for fid, pages in form_all_pages.items()
    }

    # Track "Continued from page X"
    form_continued_from: dict[str, dict[int, int]] = {}
    for fid, pages in form_all_pages.items():
        first_pages = sorted(form_first_instance_pages[fid])
        continued: dict[int, int] = {}
        for i in range(1, len(first_pages)):
            if first_pages[i] - first_pages[i - 1] <= 1:
                continued[first_pages[i]] = first_pages[i - 1]
        form_continued_from[fid] = continued

    page_ann_count: dict[int, int] = defaultdict(int)
    for field, result in zip(fields, results):
        pi = field.page_index
        if pi is None:
            continue
        fid = _form_identity(field)
        if fid and pi in form_see_pages.get(fid, set()):
            continue
        if result.resolved or result.is_not_submitted:
            page_ann_count[pi] += 1

    form_first_page: dict[str, tuple[int, str]] = {}
    form_primary_domain: dict[str, str] = {}
    form_visits: dict[str, list[tuple[str, int]]] = defaultdict(list)
    _seen_visit_form: set[tuple[str, str]] = set()
    for field, result in zip(fields, results):
        fc = field.form_code
        if fc and fc not in form_first_page and field.page_index is not None:
            form_first_page[fc] = (field.page_index, fc)
        if fc and fc not in form_primary_domain and result.sdtm_domain:
            d = result.sdtm_domain.upper()
            form_primary_domain[fc] = d[4:] if d.startswith("SUPP") else d
        visit = getattr(field, "folder", "") or ""
        if fc and visit and field.page_index is not None:
            key = (fc, visit)
            if key not in _seen_visit_form:
                _seen_visit_form.add(key)
                form_visits[fc].append((visit, field.page_index))

    domain_header_written: set[int] = set()
    see_page_written: set[str] = set()

    for field, result in zip(fields, results):
        if not result.resolved and not result.is_not_submitted:
            continue

        page_idx = field.page_index
        y = field.y
        fc = field.form_code or ""
        fid = _form_identity(field)

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
            # "Continued from page X" on subsequent pages of multi-page form
            if fid:
                prev_idx = form_continued_from.get(fid, {}).get(page_idx)
                if prev_idx is not None:
                    _draw_continued_from_page(page, prev_idx + 1, ann_x)

        # "For Annotations see page X" for repeat-visit pages
        if fid and page_idx in form_see_pages.get(fid, set()):
            see_key = f"{fid}_{page_idx}"
            if see_key not in see_page_written:
                see_page_written.add(see_key)
                first_inst = sorted(form_first_instance_pages.get(fid, []))
                _draw_see_page_reference(page, first_inst, ph, ann_x)
            continue

        eff_fs = _FONT_SIZE_DENSE if page_ann_count.get(page_idx, 0) > _DENSE_THRESHOLD else font_size

        ann_entries = _build_annotations_list(result)
        if not ann_entries:
            continue

        if len(ann_entries) > 1:
            stats["multi_domain_fields"] += 1

        def _entry_height(entry: dict) -> float:
            n_lines = 1
            if entry.get("where_clause"):
                n_lines += 1
            if entry.get("value_decode"):
                n_lines += 1
            return n_lines * (eff_fs * _FT_LINE_FACTOR) + 2 * _BOX_PADDING_Y

        # ─── PLACEMENT: Direct at field Y, clamped to page bounds ───
        slot_y = max(_PAGE_TOP_MARGIN + _entry_height(ann_entries[0]), min(y, ph - _PAGE_BOTTOM_MARGIN))

        # ─── Determine max height across all entries (for overlap tracking) ───
        max_entry_h = max(_entry_height(e) for e in ann_entries)
        box_top_for_field = slot_y - eff_fs - _BOX_PADDING_Y
        box_bottom_for_field = box_top_for_field + max_entry_h

        # ─── Check stagger for overlap with OTHER fields' annotations ───
        x_offset = tracker.get_x_offset(page_idx, box_top_for_field, box_bottom_for_field)
        base_x = max(36.0, ann_x + x_offset)

        # ─── Draw entries SIDE BY SIDE horizontally ───
        x_cursor = base_x
        any_drawn = False

        for entry in ann_entries:
            ann_text = entry["text"]
            where_clause = entry.get("where_clause", "") or ""
            value_decode = entry.get("value_decode", "") or ""
            is_derived = entry.get("is_derived", False)

            if not ann_text:
                continue

            if tracker.is_duplicate(page_idx, ann_text + where_clause, slot_y):
                stats["duplicates_skipped"] += 1
                continue

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
            wc_text = ""
            if where_clause:
                wc_text = f"where {where_clause}"
                tw = max(tw, fitz.get_text_length(wc_text, fontname=_FONT_NAME, fontsize=eff_fs))
            if value_decode:
                vd_text = f"({value_decode})"
                tw = max(tw, fitz.get_text_length(vd_text, fontname=_FONT_NAME, fontsize=eff_fs))

            box_width = tw + 2 * _BOX_PADDING_X + _FT_SIDE_INSET

            # If this box would exceed page width, wrap to next line below
            if x_cursor + box_width > pw - 10.0 and x_cursor != base_x:
                # Fallback: can't fit side-by-side, skip (rare edge case)
                continue

            box_top = slot_y - eff_fs - _BOX_PADDING_Y
            box_rect = fitz.Rect(
                x_cursor,
                box_top,
                x_cursor + box_width,
                box_top + this_h,
            )

            full_content = ann_text
            if where_clause:
                full_content += f"\n{wc_text}"
            if value_decode:
                full_content += f"\n({value_decode})"

            _freetext(
                page, box_rect, full_content,
                fontsize=eff_fs, fontname=font_n,
                text_color=text_c, fill_color=fill_c, border_color=border_c,
                border_width=_BORDER_WIDTH, dashed=use_dash,
            )

            # Advance cursor to the RIGHT for next entry
            x_cursor += box_width + _MULTI_BOX_H_GAP
            any_drawn = True
            stats["total_annotations"] += 1

        # Mark the full occupied vertical range for this field
        if any_drawn:
            tracker.mark_occupied(page_idx, box_top_for_field, box_bottom_for_field)
            stats["pages_annotated"].add(page_idx)

    ref_page = doc[0]
    _append_legend_page(doc, ref_page.rect.width, ref_page.rect.height)

    toc = _build_toc(form_first_page, form_primary_domain, dict(form_visits))
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
