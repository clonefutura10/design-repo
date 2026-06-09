"""
Usage Guard — Intelligent annotation deduplication.

DESIGN PRINCIPLE: In SDTM, findings domains (LB, VS, EG, QS, FA, RE, BE, PC)
have REPEATING structures where the SAME variable (e.g., LBORRES) is used for
every test in a panel. This is CORRECT — the test name (LBTESTCD) distinguishes
rows, not the variable name.

The guard should ONLY block TRUE duplicates:
    - Same form + same field label + same annotation = duplicate (block)
    - Same form + DIFFERENT field label + same annotation = legitimate (allow)

For non-findings domains (DM, DS, AE, CM, MH), repeated use of the same
variable on the same form IS suspicious and should be limited.
"""

from __future__ import annotations
import re
from collections import defaultdict

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DOMAIN CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

# Findings domains: repeating structures where same variable is legitimate
_FINDINGS_DOMAINS: frozenset[str] = frozenset({
    "LB", "VS", "EG", "QS", "FA", "RE", "BE", "PC", "IS", "MB",
    "PE", "DA", "IE", "TU", "TR", "RS",
})

# Maximum times the SAME (form + label + domain + variable) combo can appear
# This catches true duplicates from repeated pages
_MAX_TRUE_DUPLICATE = 200

# Maximum times the same variable can be used on a NON-findings form
# with DIFFERENT labels (catches over-matching in fallback lookups)
_MAX_VARIABLE_REUSE_NON_FINDINGS = 8

# Maximum times the same variable can be used on a findings form
# with DIFFERENT labels (essentially unlimited for legitimate structures)
_MAX_VARIABLE_REUSE_FINDINGS = 100


# ═══════════════════════════════════════════════════════════════════════════════
# TRACKING STATE
# ═══════════════════════════════════════════════════════════════════════════════

# Tracks: (form_code_upper, domain, variable, label_normalized) → count
_EXACT_USAGE: dict[tuple[str, str, str, str], int] = {}

# Tracks: (form_code_upper, domain, variable) → count of DISTINCT labels
_VARIABLE_USAGE: dict[tuple[str, str, str], int] = {}

# Tracks which labels have been assigned a given variable on a form
_VARIABLE_LABELS: dict[tuple[str, str, str], set[str]] = {}


def reset_usage_tracking() -> None:
    """Reset all tracking state between pipeline runs."""
    global _EXACT_USAGE, _VARIABLE_USAGE, _VARIABLE_LABELS
    _EXACT_USAGE = {}
    _VARIABLE_USAGE = {}
    _VARIABLE_LABELS = {}


def check_usage(
    form_code: str,
    domain: str,
    variable: str,
    field_label: str,
) -> bool:
    """
    Check if an annotation assignment is allowed (not a true duplicate).

    Returns True if the assignment should PROCEED.
    Returns False if it should be BLOCKED (duplicate).

    Logic:
        1. Same form + same label + same domain.variable → TRUE duplicate
           (Allow up to _MAX_TRUE_DUPLICATE for repeated pages)
        2. Same form + different label + same domain.variable:
           - Findings domains: ALWAYS allow (LB.LBORRES for ALT, AST, Hb = correct)
           - Non-findings domains: Allow up to _MAX_VARIABLE_REUSE_NON_FINDINGS
    """
    form_upper = form_code.upper().strip()
    domain_upper = domain.upper().strip()
    var_upper = variable.upper().strip()
    label_norm = field_label.lower().strip()

    # ── Check 1: True duplicate (exact same assignment) ──
    exact_key = (form_upper, domain_upper, var_upper, label_norm)
    exact_count = _EXACT_USAGE.get(exact_key, 0)

    if exact_count >= _MAX_TRUE_DUPLICATE:
        # This exact field+annotation appeared too many times
        # (happens when same form repeats across visits)
        return False

    # ── Check 2: Variable reuse with different labels ──
    var_key = (form_upper, domain_upper, var_upper)

    if var_key not in _VARIABLE_LABELS:
        _VARIABLE_LABELS[var_key] = set()

    is_new_label = label_norm not in _VARIABLE_LABELS[var_key]
    is_findings = domain_upper in _FINDINGS_DOMAINS

    if is_new_label:
        current_distinct = len(_VARIABLE_LABELS[var_key])
        limit = (
            _MAX_VARIABLE_REUSE_FINDINGS
            if is_findings
            else _MAX_VARIABLE_REUSE_NON_FINDINGS
        )

        if current_distinct >= limit:
            logger.debug(
                f"Usage guard: {domain}.{variable} has {current_distinct} distinct "
                f"labels on {form_code}, blocking (non-findings limit)"
            )
            return False

    # ── Record usage ──
    _EXACT_USAGE[exact_key] = exact_count + 1
    _VARIABLE_LABELS[var_key].add(label_norm)

    return True