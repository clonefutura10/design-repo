"""
Field identifier for AZ EDC Blank CRF pages.

STRATEGY: Number-Anchor Parsing
================================
In AZ EDC CRFs, every data collection field is assigned a numeric identifier
(1-3 digit number on its own line). This number is the ANCHOR.

The parsing pattern is:
    LABEL_TEXT → [FORMAT_HINT] → FIELD_NUMBER → [VALUE_OPTIONS] → next LABEL...

Two-pass approach:
    Pass 1: Identify all structural elements (headers, footers, numbers, format hints)
    Pass 2: Group content between anchors into fields

This eliminates fragile content-based heuristics for distinguishing short field
labels ("Visit date") from value options ("Yes", "No").
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum, auto


class LineRole(Enum):
    """Role of a text line in the CRF page structure."""
    HEADER = auto()
    VERSION = auto()
    FIELD_NUMBER = auto()
    FORMAT_HINT = auto()
    FOOTER = auto()
    CONTENT = auto()       # Could be label, value option, or instruction


@dataclass
class CRFField:
    """
    A single data collection field extracted from a CRF page.

    This is the unit that enters the resolution pipeline — each field
    will be matched against the AZ spec to determine its SDTM annotation.
    """

    # Identification
    field_label: str = ""
    field_number: str = ""

    # Position (for annotation placement on PDF)
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    # Content metadata
    value_options: list[str] = field(default_factory=list)
    format_hint: str = ""
    is_instruction: bool = False

    # Page context
    page_index: int = 0
    form_code: str = ""
    form_name: str = ""        # ← NEW: Form name for domain inference
    folder: str = ""

    # Contextual window (populated after all page fields identified)
    context_labels_before: list[str] = field(default_factory=list)
    context_labels_after: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Structural Detection Patterns
# ─────────────────────────────────────────────────────────────────────────────

# Header detection
_RE_STUDY_LINE = re.compile(r"^D\d+C\d+")
_RE_FOLDER_LINE = re.compile(r"^Folder:")
_RE_FORM_LINE = re.compile(r"^Form:")
_RE_GENERATED_LINE = re.compile(r"^Generated On:")
_RE_INTERNAL_NUM = re.compile(r"^\(\d+\)\s*$")
_RE_VERSION_LINE = re.compile(r"^\(Version:")

# Footer: "X of Y" page numbering
_RE_FOOTER = re.compile(r"^\d+\s+of\s+\d+\s*$")

# Field number: standalone 1-3 digit integer on its own line
_RE_FIELD_NUMBER = re.compile(r"^(\d{1,3})$")

# Format hint: "Fixed Unit: ..." or similar
_RE_FORMAT_HINT = re.compile(r"^Fixed Unit:", re.IGNORECASE)

# NOT SUBMITTED indicator patterns (from design doc Tier 1)
_NOT_SUBMITTED_PATTERNS = [
    re.compile(r"MedDRA.*(?:Code|Term\s*(?:Code|Name))", re.IGNORECASE),
    re.compile(r"^Medication\s+(?:code|dictionary\s+text)", re.IGNORECASE),
    re.compile(r"^ATC\s+(?:code|dictionary\s+text)", re.IGNORECASE),
    re.compile(r"Preferred\s+Name", re.IGNORECASE),
    re.compile(r"^Active\s+Ingredient", re.IGNORECASE),
    re.compile(r"Drug\s+Dictionary\s+Version", re.IGNORECASE),
    re.compile(r"MedDRA\s+Version", re.IGNORECASE),
    re.compile(r"Pref\.\s*Grouping", re.IGNORECASE),
]

# Instruction patterns (contextual text, not data fields)
_INSTRUCTION_PATTERNS = [
    re.compile(r"will be integrated", re.IGNORECASE),
    re.compile(r"^Only for\s", re.IGNORECASE),
    re.compile(r"^Note\s*:", re.IGNORECASE),
    re.compile(r"^Please\s", re.IGNORECASE),
    re.compile(r"^Select\s+'", re.IGNORECASE),
    re.compile(r"^If\s+the\s+", re.IGNORECASE),
    re.compile(r"^The following", re.IGNORECASE),
    re.compile(r"^Refer to", re.IGNORECASE),
    re.compile(r"^Complete\s+if\s+", re.IGNORECASE),
    re.compile(r"^This\s+form", re.IGNORECASE),
]


def _classify_structural_role(line: str, line_idx: int, total_lines: int) -> LineRole:
    """
    Classify a line's structural role (Pass 1).

    Only identifies DEFINITE structural elements. Everything else is CONTENT
    (to be resolved in Pass 2 based on position relative to field numbers).
    """
    stripped = line.strip()
    if not stripped:
        return LineRole.CONTENT  # Will be filtered as empty

    # Header block (first ~8 lines typically)
    if line_idx < 10:
        if _RE_STUDY_LINE.match(stripped):
            return LineRole.HEADER
        if _RE_FOLDER_LINE.match(stripped):
            return LineRole.HEADER
        if _RE_FORM_LINE.match(stripped):
            return LineRole.HEADER
        if _RE_GENERATED_LINE.match(stripped):
            return LineRole.HEADER
        if _RE_INTERNAL_NUM.match(stripped):
            return LineRole.HEADER

    # Version line (first page of form only)
    if _RE_VERSION_LINE.match(stripped):
        return LineRole.VERSION

    # Internal number that appears after header
    if _RE_INTERNAL_NUM.match(stripped):
        return LineRole.HEADER

    # Footer
    if _RE_FOOTER.match(stripped):
        return LineRole.FOOTER

    # Field number (standalone digits 1-999)
    if _RE_FIELD_NUMBER.match(stripped):
        return LineRole.FIELD_NUMBER

    # Format hint
    if _RE_FORMAT_HINT.match(stripped):
        return LineRole.FORMAT_HINT

    # Everything else is CONTENT — resolved by position in Pass 2
    return LineRole.CONTENT


def identify_fields_from_lines(
    lines: list[str],
    page_index: int = 0,
    form_code: str = "",
    form_name: str = "",
    folder: str = "",
) -> list[CRFField]:
    """
    Extract fields from a CRF page using number-anchor parsing.

    Two-pass approach:
        Pass 1: Classify structural roles of all lines
        Pass 2: Group content lines into fields anchored by field numbers

    The key insight: text BEFORE a field number = field label
                     text AFTER a field number (until next label) = value options

    Args:
        lines: All text lines from the page (from get_text("text").split("\\n"))
        page_index: 0-based PDF page index
        form_code: Form code from page header (e.g. "VS1", "DM")
        form_name: Form name from page header (e.g. "Vital Signs", "Demographics")
        folder: Visit/folder name from page header

    Returns:
        List of CRFField objects identified on this page.
    """
    # Filter empty lines but preserve indices for relative positioning
    indexed_lines: list[tuple[int, str]] = [
        (i, line.strip()) for i, line in enumerate(lines) if line.strip()
    ]

    if not indexed_lines:
        return []

    total = len(indexed_lines)

    # ─── Pass 1: Structural Classification ───
    classified: list[tuple[int, str, LineRole]] = []
    for pos, (orig_idx, text) in enumerate(indexed_lines):
        role = _classify_structural_role(text, pos, total)
        classified.append((orig_idx, text, role))

    # ─── Pass 2: Number-Anchored Field Assembly ───
    # Strategy:
    # 1. Find all FIELD_NUMBER positions
    # 2. For each number, look backward to collect the label
    # 3. For each number, look forward to collect value options

    fields: list[CRFField] = []

    # Find indices of all field numbers in classified list
    number_positions = [
        i for i, (_, _, role) in enumerate(classified)
        if role == LineRole.FIELD_NUMBER
    ]

    if not number_positions:
        # No field numbers on this page — try to extract any labeled content
        # This handles rare pages with fields but no numbers
        return _extract_fields_without_numbers(classified, page_index, form_code, form_name, folder)

    # Process each field number anchor
    for anchor_idx, num_pos in enumerate(number_positions):
        _, number_text, _ = classified[num_pos]

        # ─── Look BACKWARD for label ───
        # Collect content lines going backward from the number,
        # stopping at: another field number, a header/footer/version, or
        # the value options of the previous field
        label_parts: list[str] = []
        format_hint = ""

        # Determine backward boundary
        # (previous field number position, or start of content)
        if anchor_idx > 0:
            prev_num_pos = number_positions[anchor_idx - 1]
            # Start searching from the line after the previous number
            search_start = prev_num_pos + 1
        else:
            search_start = 0

        # Collect lines between search_start and num_pos
        # These lines could be: value options of prev field, then label of this field
        # The boundary between prev's options and this field's label is heuristic:
        # We take the LAST contiguous block of content before the number as the label

        backward_content: list[tuple[int, str, LineRole]] = []
        for i in range(search_start, num_pos):
            _, text, role = classified[i]
            if role == LineRole.CONTENT:
                backward_content.append(classified[i])
            elif role == LineRole.FORMAT_HINT:
                format_hint = text
            # Skip headers, footers, version lines

        # The label is the LAST contiguous block of content before the number
        # Value options of the previous field are the EARLIER content lines
        if backward_content:
            # Find where the label starts — work backward from the end
            # Label lines are typically the last 1-3 content lines before the number
            # (most labels are 1-2 lines, max 3 for long descriptive labels)
            label_lines = _extract_label_from_backward_content(backward_content)
            label_parts = [text for _, text, _ in label_lines]

        # ─── Look FORWARD for value options ───
        # Collect content lines after the number until:
        # - The next field's label begins (last content block before next number)
        # - A format hint appears (belongs to next field)
        # - A header/footer appears

        value_options: list[str] = []

        if anchor_idx < len(number_positions) - 1:
            next_num_pos = number_positions[anchor_idx + 1]
            forward_boundary = next_num_pos
        else:
            forward_boundary = len(classified)

        # Collect all content between this number and the forward boundary
        forward_content: list[tuple[int, str, LineRole]] = []
        for i in range(num_pos + 1, forward_boundary):
            _, text, role = classified[i]
            if role == LineRole.CONTENT:
                forward_content.append(classified[i])
            elif role == LineRole.FORMAT_HINT:
                # Format hint belongs to the NEXT field, stop here
                break

        # From forward content, determine what's value options vs next field's label
        # The LAST contiguous block before the next number = next field's label
        # Everything BEFORE that = value options of current field
        if anchor_idx < len(number_positions) - 1 and forward_content:
            # Split: options are all but the last block (which is next label)
            next_label_lines = _extract_label_from_backward_content(forward_content)
            next_label_start_idx = None
            if next_label_lines:
                # Find where next label starts in forward_content
                first_next_label = next_label_lines[0]
                for fc_idx, fc_item in enumerate(forward_content):
                    if fc_item[0] == first_next_label[0]:  # Match by original line index
                        next_label_start_idx = fc_idx
                        break

            if next_label_start_idx is not None and next_label_start_idx > 0:
                # Everything before the next label = value options
                value_options = [text for _, text, _ in forward_content[:next_label_start_idx]]
            elif next_label_start_idx == 0:
                # No value options — all content is next field's label
                value_options = []
            else:
                # Can't determine boundary — treat all as value options
                value_options = [text for _, text, _ in forward_content]
        else:
            # Last field on page — all forward content is value options
            value_options = [text for _, text, _ in forward_content]

        # ─── Assemble field ───
        full_label = " ".join(label_parts).strip()

        if not full_label:
            continue

        # Check if this is an instruction rather than a field
        is_instruction = _is_instruction(full_label)

        crf_field = CRFField(
            field_label=full_label,
            field_number=number_text,
            value_options=_clean_value_options(value_options),
            format_hint=format_hint,
            is_instruction=is_instruction,
            page_index=page_index,
            form_code=form_code,
            form_name=form_name,
            folder=folder,
        )
        fields.append(crf_field)

    return fields


def _extract_label_from_backward_content(
    content_lines: list[tuple[int, str, LineRole]],
) -> list[tuple[int, str, LineRole]]:
    """
    From a block of content lines, extract the label portion.

    The label is the LAST contiguous meaningful text block that is NOT
    instruction text. We work backward, collecting lines that form a
    coherent label, stopping when we hit instruction text or what looks
    like a value option boundary.
    """
    if not content_lines:
        return []

    # Filter out instruction lines from the END (they can't be labels)
    # Work backward to find the first non-instruction content
    filtered_end: list[tuple[int, str, LineRole]] = []
    hit_non_instruction = False

    for item in reversed(content_lines):
        _, text, _ = item
        if not hit_non_instruction:
            if _is_instruction(text):
                continue  # Skip trailing instructions
            else:
                hit_non_instruction = True
                filtered_end.insert(0, item)
        else:
            filtered_end.insert(0, item)

    if not filtered_end:
        # All lines were instructions — take the last content line as-is
        # (rare edge case)
        return [content_lines[-1]]

    # Now extract label from the filtered content (instruction-free end)
    # The label is the last line(s) that form a coherent phrase
    label_lines: list[tuple[int, str, LineRole]] = []

    for item in reversed(filtered_end):
        _, text, _ = item

        # Skip instruction text encountered during backward scan
        if _is_instruction(text):
            break

        if not label_lines:
            # Always take the last non-instruction line as label start
            label_lines.insert(0, item)
            continue

        # Check if this line is a continuation of the label
        if _is_label_continuation(text, label_lines):
            label_lines.insert(0, item)
        else:
            break

    return label_lines


def _is_label_continuation(text: str, existing_label_parts: list) -> bool:
    """
    Determine if a line is a continuation of a multi-line label.

    Conservative approach — only merge lines that are clearly part of
    the same phrase (wrapped long labels).
    """
    words = text.split()

    # Single-word common value options are NEVER label continuations
    if len(words) == 1:
        common_options = {
            "no", "yes", "unknown", "male", "female", "other",
            "mild", "moderate", "severe", "fatal", "asthma",
            "financial",
        }
        if text.lower() in common_options:
            return False

    # Instruction text is NEVER a label continuation
    if _is_instruction(text):
        return False

    # If the existing label starts with a lowercase letter, the previous line
    # is likely a continuation (e.g., "Optional consent to donate" + "additional...")
    if existing_label_parts:
        first_label_text = existing_label_parts[0][1]
        if first_label_text and first_label_text[0].islower():
            return True

    # Lines ending with common continuation patterns (prepositions, articles)
    continuation_endings = (
        " to", " for", " of", " in", " from", " with",
        " and", " or", " the", " a", " an",
    )
    if any(text.lower().endswith(end) for end in continuation_endings):
        return True

    # STRICT: Only merge if the text ends with a continuation pattern
    # Do NOT merge just because both lines are "long enough"
    # This prevents value options like "Optional consent to donate additional..."
    # from being merged with the actual field label

    return False


def _is_instruction(text: str) -> bool:
    """Check if a field label is actually instructional text."""
    for pattern in _INSTRUCTION_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _clean_value_options(options: list[str]) -> list[str]:
    """Clean and filter value options, removing empty/noise entries."""
    cleaned = []
    for opt in options:
        opt = opt.strip()
        if not opt:
            continue
        # Skip lines that are clearly not value options
        if _RE_FORMAT_HINT.match(opt):
            continue
        if _RE_FOOTER.match(opt):
            continue
        cleaned.append(opt)
    return cleaned


def _extract_fields_without_numbers(
    classified: list[tuple[int, str, LineRole]],
    page_index: int,
    form_code: str,
    form_name: str,
    folder: str,
) -> list[CRFField]:
    """
    Fallback extraction for pages without field numbers.

    Some pages may have content but no numbered fields (rare).
    Extract any content lines as potential fields.

    NOTE: This text-only fallback is a last resort. It cannot distinguish a
    single-word field label ("Sex", "Race") from a value option, so it drops
    1-word lines. Whenever per-line geometry is available the caller should use
    ``identify_fields_geometric`` instead, which is far more accurate.
    """
    fields = []
    for _, text, role in classified:
        if role == LineRole.CONTENT and text.strip():
            if not _is_instruction(text) and len(text.split()) >= 2:
                fields.append(CRFField(
                    field_label=text,
                    page_index=page_index,
                    form_code=form_code,
                    form_name=form_name,
                    folder=folder,
                ))
    return fields


# ─────────────────────────────────────────────────────────────────────────────
# Geometry-aware extraction (for CRFs WITHOUT numeric field anchors)
# ─────────────────────────────────────────────────────────────────────────────
#
# Many EDC exports (D9186R00001, UAT/preview builds, and a large class of
# "generic" CRFs) do NOT print a numeric anchor next to each field. On those
# pages the number-anchor parser has nothing to latch onto and the text-only
# fallback above silently drops every single-word label.
#
# These layouts are, however, highly regular GEOMETRICALLY:
#   • field LABELS sit in the left column   (x0 ≈ left page margin)
#   • value OPTIONS are right-aligned        (indented well to the right)
# so a left-column / right-column split recovers labels and their options
# cleanly. A small known-option dictionary acts as a safety net for the rare
# option that is not indented.

# Value options that may occasionally appear at the left margin; used only as a
# tie-breaker — geometry is the primary signal.
_GEOM_KNOWN_OPTIONS: frozenset[str] = frozenset({
    "yes", "no", "unknown", "not applicable", "n/a", "not done", "none",
    "male", "female", "other", "not reported", "prefer not to answer",
    "hispanic or latino", "not hispanic or latino",
    "white", "black or african american", "asian",
    "american indian or alaska native",
    "native hawaiian or other pacific islander",
    "mild", "moderate", "severe", "fatal",
    "current", "former", "never", "past", "ongoing", "present", "absent",
    "normal", "abnormal", "borderline", "clinically significant",
    "positive", "negative", "equivocal", "indeterminate",
})

_GEOM_LABEL_X_TOL = 12.0        # x0 within this of the left margin → label column
_GEOM_VALUE_MIN_INDENT = 36.0   # x0 at least this far right of margin → value option
_GEOM_LINE_GAP = 20.0           # max y-gap (pt) to merge a wrapped label continuation

# Page-chrome lines that the structural classifier misses (it only knows the
# "DnCn" study format). Covers DnRn study IDs, UAT/preview build labels, the
# "Project Name:" line and split internal-number footers — none are fields.
_RE_GEOM_CHROME = re.compile(
    r"^(?:D\d{3,}[A-Z]\d{2,}"                                    # study id (DnCn / DnRn)
    r"|Project\s+Name:\s*\S"                                     # UAT project line
    r"|UAT[_\s]"                                                 # UAT build label
    r"|[A-Za-z0-9_.\- ]{2,40}:\s*(?:All|Unique|Expanded|Matrix|All\s+Blank\s+CRF)\b"  # build title
    r"|\d*\s*\(\d+\)"                                            # internal number e.g. "25 (328)"
    r")",
    re.IGNORECASE,
)


def page_has_field_numbers(lines: list[str]) -> bool:
    """
    Return True if the page uses numeric field anchors (standalone 1-3 digit
    lines). Drives the choice between number-anchor and geometric extraction.
    """
    indexed = [(i, l.strip()) for i, l in enumerate(lines) if l.strip()]
    total = len(indexed)
    for pos, (_, text) in enumerate(indexed):
        if _classify_structural_role(text, pos, total) == LineRole.FIELD_NUMBER:
            return True
    return False


def _looks_like_label_continuation_fwd(prev_label: str, next_text: str) -> bool:
    """Conservatively decide if ``next_text`` continues a wrapped ``prev_label``."""
    nt = next_text.strip()
    if not nt or _is_instruction(nt):
        return False
    # A line starting lowercase is almost always a wrap of the previous line.
    if nt[0].islower():
        return True
    # Previous line ending on a connector word implies the phrase continues.
    pl = prev_label.strip().lower()
    connectors = (
        " to", " for", " of", " in", " from", " with", " and", " or",
        " the", " a", " an", " on", " at", " that", " which", " was", " were",
    )
    return pl.endswith(connectors)


def identify_fields_geometric(
    positioned_lines: list[tuple[float, float, float, float, str]],
    page_index: int = 0,
    form_code: str = "",
    form_name: str = "",
    folder: str = "",
) -> list[CRFField]:
    """
    Extract fields from a numberless CRF page using column geometry.

    Args:
        positioned_lines: per-line tuples ``(x0, y0, x1, y1, text)`` in reading
            order (top→bottom, left→right). Produced by the extractor from
            PyMuPDF span boxes.
        page_index, form_code, form_name, folder: page/form context.

    Returns:
        List of CRFField with field_label, value_options and x/y/width/height
        populated directly from the span geometry (no later position matching
        needed).
    """
    if not positioned_lines:
        return []

    # 1. Drop structural lines (header / footer / version / stray numbers).
    total = len(positioned_lines)
    content: list[tuple[float, float, float, float, str]] = []
    for pos, (x0, y0, x1, y1, text) in enumerate(positioned_lines):
        t = text.strip()
        if not t:
            continue
        role = _classify_structural_role(t, pos, total)
        if role in (LineRole.HEADER, LineRole.FOOTER, LineRole.VERSION,
                    LineRole.FIELD_NUMBER):
            continue
        if _RE_GEOM_CHROME.match(t):
            continue
        content.append((x0, y0, x1, y1, t))

    if not content:
        return []

    # 2. Left margin = leftmost text column on the page.
    left_margin = min(x0 for x0, _, _, _, _ in content)

    # 3. Walk lines, attaching right-column options to the active left label.
    fields: list[CRFField] = []
    cur: dict | None = None

    def _flush() -> None:
        nonlocal cur
        if cur and cur["label"].strip():
            label = cur["label"].strip()
            fld = CRFField(
                field_label=label,
                value_options=_clean_value_options(cur["options"]),
                is_instruction=_is_instruction(label),
                page_index=page_index,
                form_code=form_code,
                form_name=form_name,
                folder=folder,
            )
            fld.x = cur["x0"]
            fld.y = cur["y1"]                       # bottom of label = annotation baseline
            fld.width = max(0.0, cur["x1"] - cur["x0"])
            fld.height = max(0.0, cur["y1"] - cur["y0"])
            fields.append(fld)
        cur = None

    for x0, y0, x1, y1, text in content:
        norm = text.lower()
        is_left = x0 <= left_margin + _GEOM_LABEL_X_TOL
        is_indented = x0 >= left_margin + _GEOM_VALUE_MIN_INDENT
        known_option = norm in _GEOM_KNOWN_OPTIONS

        # Treat as a value option when indented OR a known option word
        # (and not sitting alone at the far left as a standalone label).
        if (is_indented or (known_option and not is_left)) and cur is not None:
            cur["options"].append(text)
            continue

        if is_left and not known_option:
            # New label — unless it continues the current (wrapped) label.
            if (cur is not None and not cur["options"]
                    and (y0 - cur["y1"]) <= _GEOM_LINE_GAP
                    and _looks_like_label_continuation_fwd(cur["label"], text)):
                cur["label"] = f'{cur["label"]} {text}'.strip()
                cur["x1"] = max(cur["x1"], x1)
                cur["y1"] = y1
                continue
            _flush()
            cur = {"label": text, "options": [],
                   "x0": x0, "y0": y0, "x1": x1, "y1": y1}
            continue

        # Indented/known option with no active label, or other stray content.
        if cur is not None:
            cur["options"].append(text)

    _flush()
    return fields