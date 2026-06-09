"""
Text normalization utilities for label matching.

Two normalization levels:
1. Aggressive (for exact dictionary lookup) — strips all noise
2. Gentle (for embedding input) — preserves semantic content

Also handles module name resolution:
- CRF form codes: CM1, VS1, HEVENT1, LB3, VISIT3
- Spec modules: CM, VS, HEVENT, LB, VISIT
- Strategy: try exact first, then strip trailing digits

Used by both the cache builder (indexing) and runtime matcher (querying).
"""

import re
import unicodedata


# ─────────────────────────────────────────────────────────────────────────────
# Module Name Resolution
# ─────────────────────────────────────────────────────────────────────────────

def normalize_module_name(module: str) -> str:
    """
    Normalize a module/form code for consistent lookup.

    CRF headers show "VS1", spec might have "VS1" or "vs1" — normalize to uppercase.
    """
    if not module or not module.strip():
        return ""
    return re.sub(r"\s+", "", module.strip().upper())


def get_module_variants(form_code: str) -> list[str]:
    """
    Generate all possible module name variants for a CRF form code.

    AZ convention: CRF form codes = BASE + SEQUENCE_NUMBER
        CM1, CM2 → spec module CM
        VS1 → spec module VS
        HEVENT1 → spec module HEVENT
        LB3 → spec module LB
        VISIT3 → spec module VISIT
        ASMPERF1 → spec module ASMPERF
        SPCGIB1 → spec module SPCGIB
        CONSENT1, CONSENT2 → spec module CONSENT

    But some codes DON'T have trailing digits:
        AE → AE
        DM → DM
        PE → PE

    And some codes have meaningful digits that are NOT sequence numbers:
        SU_NIC → SU_NIC (underscore variant)

    Strategy:
    1. Exact match (CM1 → CM1) — always try first
    2. Strip trailing digits (CM1 → CM) — primary fallback
    3. For underscore codes, also try prefix (SU_NIC → SU)
    4. Strip trailing letter+digit combos for edge cases (VISIT3 → VISIT)

    Returns list of module names to try, in priority order (most specific first).
    """
    if not form_code:
        return []

    code = form_code.strip().upper()
    variants = [code]  # Always try exact match first

    # Strip trailing digits: CM1 → CM, VS1 → VS, HEVENT1 → HEVENT, LB3 → LB
    stripped = re.sub(r'\d+$', '', code)
    if stripped and stripped != code and len(stripped) >= 2:
        variants.append(stripped)

    # For codes with underscore (SU_NIC), also try the base prefix
    if '_' in code:
        prefix = code.split('_')[0]
        if prefix and prefix not in variants and len(prefix) >= 2:
            variants.append(prefix)

    # For codes like SPCGIB1, also try without the SPC prefix
    # (some specs use GIB instead of SPCGIB)
    if code.startswith("SPC") and len(code) > 5:
        without_spc = code[3:]
        stripped_without_spc = re.sub(r'\d+$', '', without_spc)
        if without_spc not in variants:
            variants.append(without_spc)
        if stripped_without_spc and stripped_without_spc not in variants:
            variants.append(stripped_without_spc)

    return variants


# ─────────────────────────────────────────────────────────────────────────────
# Label Normalization
# ─────────────────────────────────────────────────────────────────────────────

def normalize_label_for_lookup(text: str) -> str:
    """
    Aggressive normalization for exact dictionary lookup in Tier 0.

    Two labels that normalize to the same string MUST be semantically
    identical. This is the primary matching key in the spec lookup cache.

    Steps:
        1. Unicode NFKC (canonical decomposition + compatibility recomposition)
        2. Lowercase
        3. Replace all whitespace variants with single space
        4. Strip leading/trailing whitespace
        5. Remove trailing punctuation (:, ., ?, *)
        6. Remove leading numbering (1., a), •, -, etc.)
        7. Collapse multiple spaces (safety)
    """
    if not text or not text.strip():
        return ""

    # NFKC handles non-breaking spaces, superscripts, ligatures
    text = unicodedata.normalize("NFKC", text)

    # Lowercase
    text = text.lower()

    # All whitespace → single space
    text = re.sub(r"[\s\u00a0\u200b\u2003\u2002\u2009]+", " ", text)

    # Strip outer
    text = text.strip()

    # Remove trailing punctuation
    text = re.sub(r"[\:\.\?\*\;]+$", "", text).strip()

    # Remove leading numbering patterns
    text = re.sub(r"^[\d]+[\.\)\:]?\s*", "", text)
    text = re.sub(r"^[a-z][\.\)]\s*", "", text)
    text = re.sub(r"^[•\-–—►▪]\s*", "", text)

    # Final collapse
    text = re.sub(r"\s+", " ", text).strip()

    return text


def normalize_label_for_embedding(text: str) -> str:
    """
    Gentle normalization for embedding model input.

    Preserves semantic content while removing only truly noisy artifacts.
    The embedding model benefits from clean but complete text.
    """
    if not text or not text.strip():
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = re.sub(r"[\s\u00a0\u200b]+", " ", text)
    text = text.strip()

    return text


# ─────────────────────────────────────────────────────────────────────────────
# Embedding Text Builders
# ─────────────────────────────────────────────────────────────────────────────

def build_embedding_text_for_spec_label(
    label: str,
    module: str = "",
    sdtm_variable: str = "",
) -> str:
    """
    Build embedding text for an AZ Spec label (Index 1).

    The embedding should capture the MEANING of the field label.
    We include the SDTM variable as a subtle hint — this helps the
    embedding model learn that "Was the ECG performed?" is close to
    "ECG Test Performed" (because both relate to EGPERF).

    We do NOT include the module name in the embedding text itself,
    because module filtering happens at search time (post-retrieval).
    Including it would bias the embedding space toward lexical module matching.
    """
    parts = []

    normalized = normalize_label_for_embedding(label)
    if normalized:
        parts.append(normalized)

    # Add SDTM label as secondary semantic context
    if sdtm_variable:
        parts.append(f"maps to {sdtm_variable.lower()}")

    return " ".join(parts) if parts else ""


def build_embedding_text_for_cdisc_variable(
    dataset: str,
    variable: str,
    label: str,
    cdisc_notes: str = "",
) -> str:
    """
    Build embedding text for a CDISC variable (Index 2).

    Rich representation: domain context + variable label + definition snippet.
    This helps the embedding capture both the variable's meaning and its domain.
    """
    parts = []

    if dataset:
        parts.append(f"{dataset.lower()} domain")

    if label:
        parts.append(normalize_label_for_embedding(label))

    if variable:
        parts.append(f"variable {variable.lower()}")

    if cdisc_notes:
        # First 120 chars of notes — enough for semantic content, not too noisy
        snippet = normalize_label_for_embedding(cdisc_notes[:120])
        if snippet:
            parts.append(snippet)

    return " ; ".join(parts) if parts else ""


def extract_form_code_from_header(form_text: str) -> str:
    """
    Extract form short code from CRF page header.

    Examples:
        "Form: Vital Signs (VS1)" → "VS1"
        "Form: Adverse Events (AE)" → "AE"
        "Demographics (DM1)" → "DM1"
    """
    if not form_text:
        return ""

    # Parenthesized code at end of line
    match = re.search(r"\(([A-Za-z0-9_]+)\)\s*$", form_text)
    if match:
        return match.group(1).upper()

    # Parenthesized code anywhere
    match = re.search(r"\(([A-Za-z0-9_]+)\)", form_text)
    if match:
        return match.group(1).upper()

    return ""

# ═══════════════════════════════════════════════════════════════════════════
# CRF-to-Spec label normalization
# Strips question prefixes/suffixes to match RAW spec labels
# ═══════════════════════════════════════════════════════════════════════════

import re as _re

_CRF_QUESTION_PREFIXES = _re.compile(
    r"^(was|is|are|were|did|does|do|has|have|had|what\s+(is|was|are|were)|"
    r"please\s+record|please\s+report|if\s+yes)\s*,?\s*(the\s+)?(subject\s+)?(have\s+)?"
    r"(any\s+)?(past\s+and/?or\s+concomitant\s+)?",
    _re.IGNORECASE,
)

_CRF_QUESTION_SUFFIXES = _re.compile(
    r"\s*\??\s*(collected|performed|done)?\s*\??\s*$",
    _re.IGNORECASE,
)

_CRF_FILLER_WORDS = _re.compile(
    r"\b(the|a|an|of|for|to|in|on|at|by|with|from|that|this|which|"
    r"subject|patient|participant)\b",
    _re.IGNORECASE,
)


def normalize_crf_label_to_spec(label: str) -> str:
    """
    Normalize a CRF field label to match RAW spec label format.
    
    CRF: "Was the result clinically significant?" → "clinically significant"
    CRF: "Was the physical examination performed?" → "physical examination performed"
    CRF: "Does the subject have any past and/or concomitant medical conditions" 
         → "medical conditions"
    CRF: "Date subject signed main informed consent" → "date signed main informed consent"
    """
    text = label.strip()
    
    # Remove trailing ?
    text = text.rstrip("?").strip()
    
    # Remove question prefixes
    text = _CRF_QUESTION_PREFIXES.sub("", text).strip()
    
    # Remove trailing "collected"/"performed" if it makes the label too short
    shortened = _CRF_QUESTION_SUFFIXES.sub("", text).strip()
    if len(shortened) > 5:
        text = shortened
    
    # Lowercase and collapse whitespace
    text = " ".join(text.lower().split())
    
    return text


def generate_spec_lookup_variants(label: str) -> list[str]:
    """
    Generate multiple normalized variants of a CRF label for spec lookup.
    Returns variants from most specific to least specific.
    """
    variants = []
    
    # Original normalized
    base = normalize_label_for_lookup(label)
    if base:
        variants.append(base)
    
    # CRF-specific normalization
    crf_norm = normalize_crf_label_to_spec(label)
    if crf_norm and crf_norm != base:
        variants.append(crf_norm)
    
    # Remove filler words version
    stripped = _CRF_FILLER_WORDS.sub(" ", crf_norm).strip()
    stripped = " ".join(stripped.split())
    if stripped and stripped != crf_norm and len(stripped) > 3:
        variants.append(stripped)
    
    return variants


# Form code to spec module resolution
_FORM_TO_MODULE: dict[str, list[str]] = {
    "VS1": ["VS"],
    "VS2": ["VS"],
    "CM1": ["CM"],
    "CM2": ["CM"],
    "LB3": ["LB"],
    "VISIT3": ["VISIT", "CONSENT"],
    "VISIT1": ["VISIT"],
    "CONSENT1": ["CONSENT"],
    "CONSENT2": ["CONSENT"],
    "ASMPERF": ["ASMPERF", "LB"],
    "ASMPERF1": ["ASMPERF1", "PULMTE"],
    "ASMPERF2": ["ASMPERF2", "PULMTE"],
    "SU_NIC": ["SU_NIC", "SU"],
    "HISS": ["HISS", "MH"],
    "IE": ["IE"],
    "IE1": ["IE"],
}


def resolve_form_to_modules(form_code: str) -> list[str]:
    """
    Resolve a CRF form code to possible spec module names.
    Returns list of module names to search in priority order.
    """
    fc = form_code.strip().upper()
    
    # Direct mapping
    if fc in _FORM_TO_MODULE:
        return _FORM_TO_MODULE[fc] + [fc]
    
    # Strip trailing digits
    base = _re.sub(r"\d+$", "", fc)
    if base != fc and base in _FORM_TO_MODULE:
        return _FORM_TO_MODULE[base] + [fc, base]
    
    # Default: try both original and base
    results = [fc]
    if base != fc:
        results.append(base)
    return results