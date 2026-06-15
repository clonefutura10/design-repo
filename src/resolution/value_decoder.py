"""
Value-level decode for controlled-response CRF fields (aCRF issue #3).

Many CRF questions are answered by selecting a controlled response option
(Yes/No, Positive/Negative, etc.). The reference aCRF annotates these with a
value-level decode showing how each response option maps to its SDTM
submission value — e.g. for "Condition ongoing? [Yes/No]" mapping to
MHENRTPT, the decode is "ONGOING = Yes".

This module is purely ADDITIVE: it produces an optional short decode string
that is rendered as an extra line under the existing variable annotation. It
never relocates or removes an annotation, so it cannot break existing output.

Design notes:
- We only emit a decode when the field's value_options clearly match a known
  controlled set AND the mapped variable is a recognized indicator/result
  variable. Otherwise we return "" (no decode) — conservative by default.
- The decode reads "<submission_value> = <response_option>" so a reviewer can
  see exactly what gets stored for each option.
"""

from __future__ import annotations
import re


def _norm(text: str) -> str:
    for ch in ("\xa0", " ", "​", "﻿"):
        text = text.replace(ch, " ")
    return re.sub(r"\s+", " ", text).strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
# Controlled response sets → {normalized_option: submission_value}
# Keyed loosely; we match by checking the field's value_options against these.
# ─────────────────────────────────────────────────────────────────────────────
_NY = {           # No/Yes Response (NCI C66742)
    "yes": "Y", "no": "N",
    "y": "Y", "n": "N",
}
_NY_UNK = {       # No/Yes/Unknown (NCI C66742 variant)
    "yes": "Y", "no": "N", "unknown": "U", "unk": "U",
}
_POS_NEG = {      # Positive/Negative result
    "positive": "POSITIVE", "negative": "NEGATIVE",
    "pos": "POSITIVE", "neg": "NEGATIVE",
}
_ENRTPT = {       # Relation to Reference Time Point — "ongoing" style
    "ongoing": "ONGOING", "yes": "ONGOING", "ongoing/continuing": "ONGOING",
    "no": "BEFORE", "completed": "BEFORE", "resolved": "BEFORE",
}

# Variables whose value is a Yes/No (or No/Yes/Unknown) indicator.
_NY_VARS = {
    "AESER", "AEOUT", "AECONTRT", "VSSTAT", "EGSTAT", "LBSTAT",
    "SVOCCUR", "RPOCCUR", "PROCCUR", "CEOCCUR", "HOOCCUR", "MHOCCUR",
    "DSOCCUR", "AEOCCUR", "VSCLSIG", "EGCLSIG", "IEORALL",
}
# Variables that are "ongoing / end-relative-to-reference-timepoint" indicators.
_ENRTPT_VARS = {"MHENRTPT", "CMENRTPT", "CEONGO", "AEENRTPT"}
# Variables whose result is positive/negative.
_POSNEG_VARS = {"LBORRES", "RPORRES"}


def _options_match(options_norm: set[str], mapping: dict[str, str]) -> bool:
    """True if at least two of the field's options are covered by the mapping."""
    covered = sum(1 for o in options_norm if o in mapping)
    return covered >= 2


def compute_value_decode(variable: str, value_options: list[str] | None) -> str:
    """
    Return a compact value-level decode string, or "" if not applicable.

    Example: variable=MHENRTPT, options=["Yes","No"]  → "ONGOING = Yes / BEFORE = No"
             variable=AESER,    options=["Yes","No"]  → "Y = Yes / N = No"
    """
    if not variable or not value_options:
        return ""

    var = variable.upper()
    options = [o for o in value_options if o and o.strip()]
    if len(options) < 2:
        return ""
    options_norm = {_norm(o) for o in options}

    # Pick the mapping table appropriate for this variable
    if var in _ENRTPT_VARS:
        table = _ENRTPT
    elif var in _NY_VARS:
        table = _NY_UNK if any(o in ("unknown", "unk") for o in options_norm) else _NY
    elif var in _POSNEG_VARS:
        table = _POS_NEG
    else:
        return ""

    if not _options_match(options_norm, table):
        return ""

    # Build decode in the order the options appear on the form
    parts: list[str] = []
    seen: set[str] = set()
    for opt in options:
        key = _norm(opt)
        sub = table.get(key)
        if sub and sub not in seen:
            seen.add(sub)
            parts.append(f"{sub} = {opt.strip()}")

    return " / ".join(parts) if len(parts) >= 2 else ""
