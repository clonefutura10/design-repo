"""
Noise Filter — Removes non-data-field entries from the resolution pipeline.

This is a critical gate between the PDF parser and the resolution engine.
Fields that pass this filter WILL be annotated (or left unresolved).
Fields that fail are silently dropped.

Noise categories:
    1. Header/metadata text that leaked through parsing
    2. Instructional/guidance text
    3. Value options mistakenly parsed as field labels
    4. Form reference lines (form names with codes in parentheses)
    5. Fragments and continuations
    6. Study-specific identifiers and version strings
"""

from __future__ import annotations
import re

from src.pdf_parser.field_identifier import CRFField
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# NOISE DETECTION PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Category 1: Header/metadata leakage ─────────────────────────────────────

# Study ID line: "D9186R00001_V1.0_08SEP2025: All Blank CRF"
_RE_STUDY_ID_LINE = re.compile(
    r"^D\d{3,}[A-Z]\d+.*(?:Blank\s*CRF|Expanded|Unique|Matrix)",
    re.IGNORECASE,
)

# Internal number in parentheses: "25 (328)", "(24582)", "(44801)"
_RE_INTERNAL_NUMBER = re.compile(r"^\d*\s*\(\d+\)\s*$")

# Page number: "1 of 948", "164 of 164"
_RE_PAGE_NUMBER = re.compile(r"^\d+\s+of\s+\d+\s*$")

# Version lines
_RE_VERSION_LINE = re.compile(r"^\(?\s*Version", re.IGNORECASE)

# Generated On
_RE_GENERATED_ON = re.compile(r"^Generated\s+On:", re.IGNORECASE)

# Study code reference
_RE_STUDY_CODE_REF = re.compile(r"^D\d{4}[A-Z]\d{4,}", re.IGNORECASE)

# "Lab Name:" standalone (form metadata, not a field)
_RE_LAB_NAME = re.compile(r"^Lab\s+Name\s*:\s*$", re.IGNORECASE)


# ─── Category 2: Instructional/guidance text ──────────────────────────────────

_INSTRUCTION_PATTERNS: list[re.Pattern] = [
    # Explicit instructions
    re.compile(r"^please\s+(record|report|fill|enter|complete|specify)", re.IGNORECASE),
    re.compile(r"^if\s+(yes|no|medication|the\s+subject|other|former|current)", re.IGNORECASE),
    re.compile(r"^for\s+(baseline|follow.?up|subjects?|each|all)\s*:", re.IGNORECASE),
    re.compile(r"^for\s+(baseline|follow.?up):\s*following", re.IGNORECASE),
    re.compile(r"^\(at\s+a\s+minimum", re.IGNORECASE),
    re.compile(r"^\(general\s+appearance", re.IGNORECASE),
    re.compile(r"^\(liver\s+and\s+spleen", re.IGNORECASE),
    re.compile(r"^head\s+and\s+neck\s+\(including", re.IGNORECASE),
    re.compile(r"^musculoskeletal\s+\(including", re.IGNORECASE),
    re.compile(r"^select\s+'", re.IGNORECASE),
    re.compile(r"^refer\s+to\b", re.IGNORECASE),
    re.compile(r"^complete\s+if\b", re.IGNORECASE),
    re.compile(r"^this\s+form\b", re.IGNORECASE),
    re.compile(r"^the\s+following\b", re.IGNORECASE),
    re.compile(r"^note\s*:", re.IGNORECASE),

    # Cross-form references
    re.compile(r"please\s+(fill|complete)\s+(in\s+)?(the|form)", re.IGNORECASE),
    re.compile(r"fill\s+in\s+(the\s+)?\w+\s+form", re.IGNORECASE),
    re.compile(r"report\s+on\s+[\"'].+[\"']\s+page", re.IGNORECASE),

    # Pack-year instructional text
    re.compile(r"^\d+\s*(cigar|cigarette|pack)", re.IGNORECASE),
    re.compile(r"^one\s+pack\s+year", re.IGNORECASE),
    re.compile(r"^examples?\s+of\s+\d+\s+pack", re.IGNORECASE),
    re.compile(r"^\d+g\s+pipe\s+tobacco", re.IGNORECASE),
    re.compile(r"^hand\s+rolled\s+cigarette", re.IGNORECASE),
    re.compile(r"^also,", re.IGNORECASE),
    re.compile(r"^1/2\s+pack\s+\(", re.IGNORECASE),
    re.compile(r"^2\s+pack\s+\(", re.IGNORECASE),

    # Conditional/referential instructions
    re.compile(r"^any\s+medication\s+for\s+\w+\s+used\s+during", re.IGNORECASE),
    re.compile(r"^if\s+an\s+exacerbation\s+visit", re.IGNORECASE),
    re.compile(r"^by\s+clicking\s+the", re.IGNORECASE),
    re.compile(r"^if\s+yes,?\s*please\s+record", re.IGNORECASE),

    # Continuation fragments that leaked
    re.compile(r"^since\s+last\s+visit\s*\??$", re.IGNORECASE),
    re.compile(r"^last\s+visit\s*\??$", re.IGNORECASE),
    re.compile(r"^physician\s+visits?\s*$", re.IGNORECASE),
    re.compile(r"^during\s+the\s+study\s*$", re.IGNORECASE),
    re.compile(r"^continue\s+staying\s+in\s+the\s*$", re.IGNORECASE),
    re.compile(r"^any\s+detailed\s+information\s*$", re.IGNORECASE),
    re.compile(r"^study\s+by\s+the\s+investigator\s+or\s*$", re.IGNORECASE),
    re.compile(r"^previous\s+12\s+months\s*\??\s*$", re.IGNORECASE),
]


# ─── Category 3: Form reference lines (form name + code in parentheses) ──────
# These appear when the UNS (unscheduled) form lists available forms:
# "Brief Physical Examination(PE2)", "Vital Signs(VS)", etc.

_RE_FORM_REFERENCE = re.compile(
    r"^[A-Z][A-Za-z\s_\-/]+\([A-Z][A-Z0-9_]+\)\s*$"
)


# ─── Category 4: Known value option patterns (not field labels) ───────────────

# Dose frequency options
_RE_FREQUENCY_OPTION = re.compile(
    r"^\d+\s+times?\s+per\s+(day|week|month)$", re.IGNORECASE
)
_RE_EVERY_OPTION = re.compile(
    r"^every\s+(\d+\s+)?(hour|day|week|other\s+day)s?$", re.IGNORECASE
)
_RE_PER_OPTION = re.compile(r"^per\s+(day|week|month)$", re.IGNORECASE)
_RE_AS_NEEDED = re.compile(r"^as\s+needed$", re.IGNORECASE)

# Short known value options (exact matches)
_KNOWN_VALUE_OPTIONS: frozenset[str] = frozenset({
    # Common yes/no/unknown
    "yes", "no", "unknown", "not applicable", "not done",
    # Sex
    "male", "female",
    # Status
    "normal", "abnormal", "borderline",
    "current", "former", "never",
    "past", "ongoing",
    # Severity
    "mild", "moderate", "severe", "fatal",
    # Lab significance
    "clinically significant",
    # AE outcomes (these are value options, not fields)
    "not recovered/not resolved",
    "recovered/resolved with",
    # Action taken options
    "dose not changed",
    "dose reduced",
    "drug interrupted",
    "drug withdrawn",
    # SAE criteria fragments
    "existing hospitalization",
    # Contact modes
    "on-site visit", "remote-audio", "remote-video", "home visit",
    # Consent categories (these are value options)
    "full withdrawal from the",
    "main study and all of the",
    "patient withdrew informed",
    "consent without providing",
    "usage of coded data and",
    "usage of coded data for",
    "biosamples for future",
    "future research",
    "additional options",
    "astrazeneca anytime",
    # DS reason options
    "lost to follow-up", "withdrawal by subject",
    "consent withdrawal",
    "deemed not suitable to",
    # Route options
    "auricular (otic)",
    # Drug class options
    "inhaled antibiotic", "inhaled corticosteroid",
    # Lab color/appearance options
    "faint yellow", "light red", "large amount",
    "dark yellow", "red", "amber", "clear", "cloudy", "turbid",
})

# Medication class fragments (value options that leaked)
_RE_MEDICATION_CLASS = re.compile(
    r"^(laba|ltra|lama|saba|ics)\s*\(", re.IGNORECASE
)


# ─── Category 5: Fragments too short or malformed to be fields ────────────────

_MIN_FIELD_LENGTH = 3  # Minimum characters for a valid field label


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN FILTER FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def is_noise_field(field: CRFField) -> bool:
    """
    Determine if a CRFField is noise (should NOT enter resolution).

    Returns True if the field should be EXCLUDED from the pipeline.
    Returns False if the field should PROCEED to resolution.

    Args:
        field: A CRFField extracted from the PDF parser.

    Returns:
        True = noise (exclude), False = data field (keep).
    """
    label = field.field_label.strip()

    # ── Empty or too short ──
    if not label or len(label) < _MIN_FIELD_LENGTH:
        return True

    # ── Category 1: Header/metadata leakage ──
    if _RE_STUDY_ID_LINE.match(label):
        return True
    if _RE_INTERNAL_NUMBER.match(label):
        return True
    if _RE_PAGE_NUMBER.match(label):
        return True
    if _RE_VERSION_LINE.match(label):
        return True
    if _RE_GENERATED_ON.match(label):
        return True
    if _RE_STUDY_CODE_REF.match(label) and len(label) < 40:
        return True
    if _RE_LAB_NAME.match(label):
        return True

    # ── Category 2: Instructional text ──
    for pattern in _INSTRUCTION_PATTERNS:
        if pattern.search(label):
            return True

    # ── Category 3: Form reference lines ──
    # "Brief Physical Examination(PE2)", "Vital Signs(VS)"
    if _RE_FORM_REFERENCE.match(label):
        return True

    # ── Category 4: Known value options ──
    label_lower = label.lower().strip()

    if label_lower in _KNOWN_VALUE_OPTIONS:
        return True

    if _RE_FREQUENCY_OPTION.match(label):
        return True
    if _RE_EVERY_OPTION.match(label):
        return True
    if _RE_PER_OPTION.match(label):
        return True
    if _RE_AS_NEEDED.match(label):
        return True
    if _RE_MEDICATION_CLASS.match(label):
        return True

    # ── Category 5: Field already marked as instruction ──
    if field.is_instruction:
        return True

    # ── Category 6: "Concomitant Medications for NCFBE (CM1)" page." fragments ──
    if label.endswith('" page.') or label.endswith("\" page."):
        return True

    # ── Not noise — proceed to resolution ──
    return False