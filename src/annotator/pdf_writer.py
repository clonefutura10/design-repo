"""
Professional aCRF PDF Annotation Writer.

Features:
- Colour-coded boxes by SDTM domain class
- Separate boxes for multi-domain mappings (stacked vertically)
- Multiple dataset headers per page (one per domain present)
- Black text inside boxes
- Codelist codes shown in annotations
- PDF bookmarks/TOC for each form
- Robust overlap avoidance for adjacent annotations
- Deduplication of identical annotations at same Y position
- Adaptive font sizing for dense pages
- Form-boundary-aware domain headers (no bleeding between forms)
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

_TEXT_COLOUR = (0.0, 0.0, 0.0)
_NOT_SUBMITTED_TEXT = (0.35, 0.35, 0.35)
_NOT_SUBMITTED_BORDER = (0.55, 0.55, 0.55)
_NOT_SUBMITTED_FILL = (0.94, 0.94, 0.94)

_HEADER_FONT_SIZE = 7.0
_BORDER_WIDTH = 0.5
_FONT_SIZE = 6.0
_FONT_SIZE_DENSE = 5.0       # For pages with many annotations
_FONT_NAME = "helv"
_ANNOTATION_X_RATIO = 0.58   # Push annotations into right margin
_MIN_Y_GAP = 9.0             # Minimum gap between annotation boxes
_BOX_PADDING_X = 2.5
_BOX_PADDING_Y = 1.5
_PAGE_TOP_MARGIN = 55.0      # Don't place annotations above this
_PAGE_BOTTOM_MARGIN = 25.0   # Don't place annotations below this
_DENSE_PAGE_THRESHOLD = 12   # Pages with more annotations use smaller font
_MULTI_BOX_SPACING = 2.0     # Gap between stacked boxes for same field


# =============================================================================
# SDTM Domain Full Names
# =============================================================================

_DOMAIN_NAMES: dict[str, str] = {
    "AE": "ADVERSE EVENTS",
    "BE": "BIOSPECIMEN EVENTS",
    "CE": "CLINICAL EVENTS",
    "CM": "CONCOMITANT MEDICATIONS",
    "CO": "COMMENTS",
    "DD": "DEATH DETAILS",
    "DM": "DEMOGRAPHICS",
    "DS": "DISPOSITION",
    "EC": "EXPOSURE AS COLLECTED",
    "EG": "ECG TEST RESULTS",
    "EX": "EXPOSURE",
    "FA": "FINDINGS ABOUT",
    "FACE": "FINDINGS ABOUT - CLINICAL EVENTS",
    "FAHO": "FINDINGS ABOUT - HEALTHCARE ENCOUNTERS",
    "HO": "HEALTHCARE ENCOUNTERS",
    "IE": "INCLUSION/EXCLUSION CRITERIA",
    "IS": "IMMUNOGENICITY SPECIMEN",
    "LB": "LABORATORY TEST RESULTS",
    "MB": "MICROBIOLOGY SPECIMEN",
    "MH": "MEDICAL HISTORY",
    "PC": "PHARMACOKINETICS CONCENTRATIONS",
    "PE": "PHYSICAL EXAMINATION",
    "PR": "PROCEDURES",
    "QS": "QUESTIONNAIRES",
    "RE": "RESPIRATORY SYSTEM FINDINGS",
    "RP": "REPRODUCTIVE SYSTEM FINDINGS",
    "SC": "SUBJECT CHARACTERISTICS",
    "SU": "SUBSTANCE USE",
    "SV": "SUBJECT VISITS",
    "TI": "TRIAL INCLUSION/EXCLUSION",
    "TU": "TUMOR IDENTIFICATION",
    "TR": "TUMOR/LESION RESULTS",
    "RS": "DISEASE RESPONSE",
    "VS": "VITAL SIGNS",
    # Supplemental domains
    "SUPPDM": "SUPPLEMENTAL DEMOGRAPHICS",
    "SUPPAE": "SUPPLEMENTAL ADVERSE EVENTS",
    "SUPPCM": "SUPPLEMENTAL CONCOMITANT MEDICATIONS",
    "SUPPCE": "SUPPLEMENTAL CLINICAL EVENTS",
    "SUPPEC": "SUPPLEMENTAL EXPOSURE AS COLLECTED",
    "SUPPEG": "SUPPLEMENTAL ECG TEST RESULTS",
    "SUPPEX": "SUPPLEMENTAL EXPOSURE",
    "SUPPFA": "SUPPLEMENTAL FINDINGS ABOUT",
    "SUPPHO": "SUPPLEMENTAL HEALTHCARE ENCOUNTERS",
    "SUPPIE": "SUPPLEMENTAL INCLUSION/EXCLUSION",
    "SUPPIS": "SUPPLEMENTAL IMMUNOGENICITY SPECIMEN",
    "SUPPLB": "SUPPLEMENTAL LABORATORY TEST RESULTS",
    "SUPPMH": "SUPPLEMENTAL MEDICAL HISTORY",
    "SUPPPC": "SUPPLEMENTAL PK CONCENTRATIONS",
    "SUPPPR": "SUPPLEMENTAL PROCEDURES",
    "SUPPQS": "SUPPLEMENTAL QUESTIONNAIRES",
    "SUPPSU": "SUPPLEMENTAL SUBSTANCE USE",
    "SUPPVS": "SUPPLEMENTAL VITAL SIGNS",
}


def _get_domain_full_name(domain: str) -> str:
    """Get full dataset name for display in header."""
    d = domain.upper()
    if d in _DOMAIN_NAMES:
        return _DOMAIN_NAMES[d]
    if d.startswith("SUPP") and d in _DOMAIN_NAMES:
        return _DOMAIN_NAMES[d]
    return d


# =============================================================================
# Domain Colour Map - (border_colour, fill_colour)
# =============================================================================

_DOMAIN_COLOURS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    # Events - Red/Crimson
    "AE":   ((0.65, 0.05, 0.05), (1.0, 0.93, 0.93)),
    "CE":   ((0.65, 0.05, 0.05), (1.0, 0.93, 0.93)),
    "MH":   ((0.65, 0.05, 0.05), (1.0, 0.93, 0.93)),

    # Interventions - Forest Green
    "CM":   ((0.05, 0.42, 0.05), (0.91, 1.0, 0.91)),
    "EC":   ((0.05, 0.42, 0.05), (0.91, 1.0, 0.91)),
    "EX":   ((0.05, 0.42, 0.05), (0.91, 1.0, 0.91)),
    "PR":   ((0.05, 0.42, 0.05), (0.91, 1.0, 0.91)),

    # Findings - Royal Blue
    "VS":   ((0.05, 0.10, 0.62), (0.91, 0.93, 1.0)),
    "LB":   ((0.05, 0.10, 0.62), (0.91, 0.93, 1.0)),
    "EG":   ((0.05, 0.10, 0.62), (0.91, 0.93, 1.0)),
    "QS":   ((0.05, 0.10, 0.62), (0.91, 0.93, 1.0)),
    "FA":   ((0.05, 0.10, 0.62), (0.91, 0.93, 1.0)),
    "IS":   ((0.05, 0.10, 0.62), (0.91, 0.93, 1.0)),
    "MB":   ((0.05, 0.10, 0.62), (0.91, 0.93, 1.0)),
    "BE":   ((0.05, 0.10, 0.62), (0.91, 0.93, 1.0)),
    "RE":   ((0.05, 0.10, 0.62), (0.91, 0.93, 1.0)),

    # Special Purpose - Purple
    "DM":   ((0.40, 0.05, 0.55), (0.95, 0.91, 1.0)),
    "DS":   ((0.40, 0.05, 0.55), (0.95, 0.91, 1.0)),
    "IE":   ((0.40, 0.05, 0.55), (0.95, 0.91, 1.0)),
    "SV":   ((0.40, 0.05, 0.55), (0.95, 0.91, 1.0)),
    "TI":   ((0.40, 0.05, 0.55), (0.95, 0.91, 1.0)),
    "SU":   ((0.40, 0.05, 0.55), (0.95, 0.91, 1.0)),
    "SC":   ((0.40, 0.05, 0.55), (0.95, 0.91, 1.0)),

    # Other - Burnt Orange
    "DD":   ((0.55, 0.30, 0.0),  (1.0, 0.95, 0.88)),
    "HO":   ((0.55, 0.30, 0.0),  (1.0, 0.95, 0.88)),
    "CO":   ((0.55, 0.30, 0.0),  (1.0, 0.95, 0.88)),
    "RP":   ((0.55, 0.30, 0.0),  (1.0, 0.95, 0.88)),

    # Findings About - Teal
    "FACE": ((0.0, 0.42, 0.50),  (0.89, 0.98, 1.0)),
    "FAHO": ((0.0, 0.42, 0.50),  (0.89, 0.98, 1.0)),

    # Pharmacokinetics - Dark Teal
    "PC":   ((0.0, 0.38, 0.38),  (0.89, 0.98, 0.98)),

    # Oncology - Dark Red/Maroon
    "TU":   ((0.50, 0.0, 0.15),  (1.0, 0.91, 0.93)),
    "TR":   ((0.50, 0.0, 0.15),  (1.0, 0.91, 0.93)),
    "RS":   ((0.50, 0.0, 0.15),  (1.0, 0.91, 0.93)),
}

_DEFAULT_BORDER = (0.3, 0.3, 0.3)
_DEFAULT_FILL = (0.95, 0.95, 0.95)


def _get_domain_colours(domain: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Get (border_colour, fill_colour) for a domain."""
    d = domain.upper()
    if d in _DOMAIN_COLOURS:
        return _DOMAIN_COLOURS[d]
    if d.startswith("SUPP"):
        base = d[4:]
        if base in _DOMAIN_COLOURS:
            return _DOMAIN_COLOURS[base]
    return (_DEFAULT_BORDER, _DEFAULT_FILL)


def _get_header_colours(domain: str) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Get (text_colour, border_colour, fill_colour) for the domain HEADER."""
    border, fill = _get_domain_colours(domain)
    header_text = border
    header_border = border
    header_fill = (
        fill[0] * 0.95,
        fill[1] * 0.95,
        fill[2] * 0.95,
    )
    return (header_text, header_border, header_fill)


# =============================================================================
# ANNOTATION ENTRY BUILDER — Separate box per domain mapping
# =============================================================================

def _build_annotations_list(result: ResolutionResult) -> list[dict]:
    """
    Build a list of separate annotation entries (one per domain mapping).
    Each entry gets its own individually-colored box on the PDF.

    Returns list of dicts: {"text": str, "domain": str, "is_not_submitted": bool}
    """
    annotations = []

    if result.is_not_submitted:
        annotations.append({
            "text": "NOT SUBMITTED",
            "domain": "",
            "is_not_submitted": True,
        })
        return annotations

    if not result.sdtm_domain or not result.sdtm_variable:
        return annotations

    # ── Primary mapping ──
    if result.is_supplemental:
        domain = result.sdtm_domain.upper()
        prefix = domain if domain.startswith("SUPP") else f"SUPP{domain}"
        text = f"{prefix}.{result.sdtm_variable}"
    else:
        text = f"{result.sdtm_domain}.{result.sdtm_variable}"

    if result.codelist_code:
        text += f" ({result.codelist_code})"

    base_domain = result.sdtm_domain.upper()
    if base_domain.startswith("SUPP"):
        base_domain = base_domain[4:]

    annotations.append({
        "text": text,
        "domain": base_domain,
        "is_not_submitted": False,
    })

    # ── Additional mappings — each as SEPARATE box with own color ──
    if hasattr(result, 'additional_mappings') and result.additional_mappings:
        for mapping in result.additional_mappings:
            add_domain = mapping.get("domain", "") or mapping.get("sdtm_domain", "")
            add_variable = mapping.get("variable", "") or mapping.get("sdtm_variable", "")
            add_codelist = mapping.get("codelist", "") or mapping.get("codelist_code", "")
            add_is_supp = mapping.get("is_supp", False) or mapping.get("is_supplemental", False)

            if not add_domain or not add_variable:
                continue

            add_domain = add_domain.upper()

            if add_is_supp:
                prefix = add_domain if add_domain.startswith("SUPP") else f"SUPP{add_domain}"
                add_text = f"{prefix}.{add_variable}"
            else:
                add_text = f"{add_domain}.{add_variable}"

            if add_codelist:
                add_text += f" ({add_codelist})"

            ann_domain = add_domain
            if ann_domain.startswith("SUPP"):
                ann_domain = ann_domain[4:]

            annotations.append({
                "text": add_text,
                "domain": ann_domain,
                "is_not_submitted": False,
            })

    return annotations


# =============================================================================
# PAGE DOMAIN HELPERS — Form-boundary aware
# =============================================================================

def _get_all_domains_for_page(
    results_on_page: list[ResolutionResult],
    form_code: str = "",
) -> list[str]:
    """
    Get ALL unique base domains present on a page, ordered by frequency.
    Uses form→domain mapping as AUTHORITATIVE source, then supplements
    with domains from resolved annotations.
    """
    domain_counts: dict[str, int] = defaultdict(int)

    # Priority 1: Form→domain mapping (prevents domain bleeding)
    if form_code:
        from src.resolution.tier0_rules import _get_domain_for_form
        mapped_domain = _get_domain_for_form(form_code)
        if mapped_domain:
            # Give it a large weight so it always appears first
            domain_counts[mapped_domain] += 1000

    # Priority 2: Vote from resolved annotations
    for r in results_on_page:
        if not r.resolved or r.is_not_submitted:
            continue
        if r.sdtm_domain:
            d = r.sdtm_domain.upper()
            if d.startswith("SUPP"):
                d = d[4:]
            domain_counts[d] += 1
        # Also count additional mapping domains
        if hasattr(r, 'additional_mappings') and r.additional_mappings:
            for m in r.additional_mappings:
                ad = m.get("domain", "") or m.get("sdtm_domain", "")
                if ad:
                    ad = ad.upper()
                    if ad.startswith("SUPP"):
                        ad = ad[4:]
                    domain_counts[ad] += 1

    # Sort by frequency descending
    return [d for d, _ in sorted(domain_counts.items(), key=lambda x: -x[1])]


# =============================================================================
# DATASET HEADERS — Multiple per page (one per domain, stacked)
# =============================================================================

def _draw_dataset_headers(page: fitz.Page, domains: list[str], ann_x: float):
    """
    Draw dataset full name header(s) at top-right of form page.
    One header box per domain present on the page, stacked vertically.
    Each header uses its own domain colour scheme.
    """
    if not domains:
        return

    header_y_start = 42.0
    header_spacing = _HEADER_FONT_SIZE + 8.0

    for i, domain in enumerate(domains):
        full_name = _get_domain_full_name(domain)
        header_text = f"{domain} ({full_name})"
        header_y = header_y_start + (i * header_spacing)

        header_text_colour, header_border_colour, header_fill_colour = _get_header_colours(domain)

        text_width = fitz.get_text_length(
            header_text,
            fontname=_FONT_NAME,
            fontsize=_HEADER_FONT_SIZE,
        )

        box_rect = fitz.Rect(
            ann_x - _BOX_PADDING_X,
            header_y - _HEADER_FONT_SIZE - 3,
            ann_x + text_width + _BOX_PADDING_X,
            header_y + 3,
        )

        page.draw_rect(
            box_rect,
            color=header_border_colour,
            fill=header_fill_colour,
            width=0.8,
            overlay=True,
        )

        page.insert_text(
            fitz.Point(ann_x, header_y),
            header_text,
            fontsize=_HEADER_FONT_SIZE,
            color=header_text_colour,
            fontname=_FONT_NAME,
        )


# =============================================================================
# OVERLAP AVOIDANCE
# =============================================================================

class _OverlapTracker:
    """Tracks placed annotation positions per page and finds non-overlapping Y."""

    def __init__(self):
        self.occupied: dict[int, list[tuple[float, float]]] = defaultdict(list)
        self.placed_texts: dict[int, set[str]] = defaultdict(set)

    def is_duplicate(self, page_idx: int, ann_text: str, desired_y: float = 0.0,
                     y_tolerance: float = 3.0) -> bool:
        """
        Per-page deduplication: each unique annotation text appears only ONCE per page.
        """
        if ann_text in self.placed_texts[page_idx]:
            return True
        self.placed_texts[page_idx].add(ann_text)
        return False

    def find_slot(self, page_idx: int, desired_y: float, box_height: float,
                  page_height: float) -> float:
        """Find a non-overlapping Y position for an annotation box."""
        min_y = _PAGE_TOP_MARGIN
        max_y = page_height - _PAGE_BOTTOM_MARGIN

        desired_y = max(min_y + box_height, min(desired_y, max_y))

        occupied = self.occupied[page_idx]
        gap = 1.5

        def _overlaps(y: float) -> bool:
            new_top = y - box_height - gap
            new_bottom = y + gap
            for (occ_top, occ_bottom) in occupied:
                if new_top < occ_bottom and new_bottom > occ_top:
                    return True
            return False

        # Try desired position
        if not _overlaps(desired_y):
            self._register(page_idx, desired_y, box_height, gap)
            return desired_y

        # Search downward
        y = desired_y
        for _ in range(60):
            y += box_height + gap
            if y > max_y:
                break
            if not _overlaps(y):
                self._register(page_idx, y, box_height, gap)
                return y

        # Search upward
        y = desired_y
        for _ in range(60):
            y -= box_height + gap
            if y < min_y + box_height:
                break
            if not _overlaps(y):
                self._register(page_idx, y, box_height, gap)
                return y

        # Fallback
        fallback_y = max_y - (len(occupied) % 8) * (box_height + gap)
        self._register(page_idx, fallback_y, box_height, gap)
        return fallback_y

    def _register(self, page_idx: int, y: float, box_height: float, gap: float):
        """Register an occupied range."""
        top = y - box_height - gap
        bottom = y + gap
        self.occupied[page_idx].append((top, bottom))

    def reserve_header(self, page_idx: int, num_headers: int = 1):
        """Reserve space for dataset header(s) at top of page."""
        header_height = num_headers * (_HEADER_FONT_SIZE + 8.0)
        self.occupied[page_idx].append(
            (_PAGE_TOP_MARGIN - 15, _PAGE_TOP_MARGIN + header_height)
        )


# =============================================================================
# MAIN FUNCTION
# =============================================================================

def annotate_pdf(
    input_pdf_path: Path,
    output_pdf_path: Path,
    results: list[ResolutionResult],
    fields: list[CRFField],
    font_size: float = _FONT_SIZE,
) -> dict:
    """
    Write professional aCRF annotations onto the PDF.

    Multi-domain mappings render as separate stacked boxes,
    each with its own domain colour.
    """
    if len(results) != len(fields):
        raise ValueError(
            f"results ({len(results)}) and fields ({len(fields)}) length mismatch"
        )

    doc = fitz.open(str(input_pdf_path))

    stats = {
        "total_annotations": 0,
        "pages_annotated": set(),
        "not_submitted": 0,
        "skipped_no_position": 0,
        "duplicates_skipped": 0,
        "multi_domain_fields": 0,
    }

    tracker = _OverlapTracker()

    # ── Pre-compute: group results and fields by page ──
    page_results: dict[int, list[ResolutionResult]] = defaultdict(list)
    page_annotation_count: dict[int, int] = defaultdict(int)
    page_form_codes: dict[int, set[str]] = defaultdict(set)

    for field, result in zip(fields, results):
        if field.page_index is not None:
            page_results[field.page_index].append(result)
            if field.form_code:
                page_form_codes[field.page_index].add(field.form_code)
            if result.resolved or result.is_not_submitted:
                page_annotation_count[field.page_index] += 1

    # ── Track which form+page combos have had their header written ──
    form_header_written: set[str] = set()

    # ── Track form first pages for bookmarks ──
    form_first_page: dict[str, tuple[int, str]] = {}
    for field, result in zip(fields, results):
        if field.form_code and field.form_code not in form_first_page:
            if field.page_index is not None:
                form_first_page[field.form_code] = (field.page_index, field.form_code)

    # ── Write annotations ──
    for field, result in zip(fields, results):

        if not result.resolved and not result.is_not_submitted:
            continue

        page_idx = field.page_index
        y = field.y

        if page_idx is None or y is None or y == 0.0:
            stats["skipped_no_position"] += 1
            continue

        page = doc[page_idx]
        page_width = page.rect.width
        page_height = page.rect.height
        ann_x = page_width * _ANNOTATION_X_RATIO

        # ── Adaptive font size for dense pages ──
        count_on_page = page_annotation_count.get(page_idx, 0)
        if count_on_page > _DENSE_PAGE_THRESHOLD:
            effective_font_size = _FONT_SIZE_DENSE
        else:
            effective_font_size = font_size

        # ── Write dataset headers on first occurrence of form on this page ──
        form_page_key = f"{field.form_code}_{page_idx}"
        if field.form_code and form_page_key not in form_header_written:
            form_header_written.add(form_page_key)

            # Determine primary form_code for this page (single form = authoritative)
            fc_set = page_form_codes.get(page_idx, set())
            page_form_code = fc_set.pop() if len(fc_set) == 1 else field.form_code

            page_domains = _get_all_domains_for_page(
                page_results.get(page_idx, []),
                form_code=page_form_code,
            )
            if page_domains:
                _draw_dataset_headers(page, page_domains, ann_x)
                tracker.reserve_header(page_idx, num_headers=len(page_domains))

        # ── Build all annotation entries for this field ──
        ann_entries = _build_annotations_list(result)
        if not ann_entries:
            continue

        # Track multi-domain fields
        if len(ann_entries) > 1:
            stats["multi_domain_fields"] += 1

        # ── Calculate total height for stacked boxes ──
        single_box_height = effective_font_size + 2 * _BOX_PADDING_Y
        total_stack_height = (single_box_height + _MULTI_BOX_SPACING) * len(ann_entries)

        # ── Find non-overlapping Y position for the stack ──
        target_y = tracker.find_slot(page_idx, y, total_stack_height, page_height)

        # ── Draw each annotation entry as a separate colored box ──
        y_offset = 0.0
        any_drawn = False

        for entry in ann_entries:
            ann_text = entry["text"]
            if not ann_text:
                continue

            # Deduplication check
            text_y = target_y + y_offset
            if tracker.is_duplicate(page_idx, ann_text, text_y):
                stats["duplicates_skipped"] += 1
                continue

            # Get colours for THIS specific domain
            if entry["is_not_submitted"]:
                border_colour = _NOT_SUBMITTED_BORDER
                fill_colour = _NOT_SUBMITTED_FILL
                text_colour = _NOT_SUBMITTED_TEXT
                stats["not_submitted"] += 1
            else:
                border_colour, fill_colour = _get_domain_colours(entry["domain"])
                text_colour = _TEXT_COLOUR

            # Calculate box dimensions
            text_width = fitz.get_text_length(
                ann_text, fontname=_FONT_NAME, fontsize=effective_font_size
            )

            box_rect = fitz.Rect(
                ann_x - _BOX_PADDING_X,
                text_y - effective_font_size - _BOX_PADDING_Y,
                ann_x + text_width + _BOX_PADDING_X,
                text_y + _BOX_PADDING_Y,
            )

            # Draw coloured box
            page.draw_rect(
                box_rect,
                color=border_colour,
                fill=fill_colour,
                width=_BORDER_WIDTH,
                overlay=True,
            )

            # Insert text
            page.insert_text(
                fitz.Point(ann_x, text_y),
                ann_text,
                fontsize=effective_font_size,
                color=text_colour,
                fontname=_FONT_NAME,
            )

            # Shift down for next box in stack
            y_offset += single_box_height + _MULTI_BOX_SPACING
            any_drawn = True
            stats["total_annotations"] += 1

        if any_drawn:
            stats["pages_annotated"].add(page_idx)

    # ── Add bookmarks/TOC ──
    toc = []
    for form_code in sorted(form_first_page.keys(), key=lambda x: form_first_page[x][0]):
        page_idx, label = form_first_page[form_code]
        toc.append([1, f"{label}", page_idx + 1])

    if toc:
        try:
            doc.set_toc(toc)
        except Exception as e:
            logger.warning(f"Could not set TOC/bookmarks: {e}")

    # ── Finalize ──
    stats["pages_annotated"] = len(stats["pages_annotated"])

    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_pdf_path))
    doc.close()

    logger.info(
        f"PDF annotated: {stats['total_annotations']} annotations on "
        f"{stats['pages_annotated']} pages, "
        f"{stats['multi_domain_fields']} multi-domain fields, "
        f"{stats['duplicates_skipped']} duplicates skipped"
    )

    return stats