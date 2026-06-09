"""
Rule Loader — Loads domain-scoped rules from JSON configuration files.

Separates rule DATA from the matching ENGINE. Rules are stored in
config/rules/*.json and compiled at startup.

This is the extensibility mechanism: to add rules for a new therapeutic area,
add a new JSON file — no Python code changes needed.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DomainRule:
    """A compiled domain-scoped matching rule."""
    domain: str                  # Target SDTM domain (e.g., "LB")
    label_pattern: re.Pattern   # Compiled regex for field label
    variable: str               # Target SDTM variable (e.g., "LBORRES")
    codelist: str               # Codelist code (or "")
    is_supp: bool               # Whether this maps to SUPPxx
    note: str                   # Human-readable description


# ═══════════════════════════════════════════════════════════════════════════════
# LOADER
# ═══════════════════════════════════════════════════════════════════════════════

_RULES_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "rules"
_compiled_domain_rules: list[DomainRule] = []
_loaded = False


def _compile_rules_from_file(filepath: Path) -> list[DomainRule]:
    """Load and compile rules from a single JSON file."""
    if not filepath.exists():
        logger.warning(f"Rules file not found: {filepath}")
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    rules = []
    raw_rules = data.get("rules", [])

    for i, entry in enumerate(raw_rules):
        try:
            compiled = DomainRule(
                domain=entry["domain"].upper(),
                label_pattern=re.compile(entry["label_pattern"], re.IGNORECASE),
                variable=entry["variable"].upper(),
                codelist=entry.get("codelist", ""),
                is_supp=entry.get("is_supp", False),
                note=entry.get("note", ""),
            )
            rules.append(compiled)
        except (KeyError, re.error) as e:
            logger.warning(f"Skipping invalid rule #{i} in {filepath.name}: {e}")

    logger.info(f"Loaded {len(rules)} domain rules from {filepath.name}")
    return rules


def load_all_domain_rules() -> list[DomainRule]:
    """
    Load and compile all domain-scoped rules from config/rules/*.json.

    Called once at startup. Results are cached module-level.
    """
    global _compiled_domain_rules, _loaded

    if _loaded:
        return _compiled_domain_rules

    _compiled_domain_rules = []

    if not _RULES_DIR.exists():
        _RULES_DIR.mkdir(parents=True, exist_ok=True)
        logger.warning(f"Created empty rules directory: {_RULES_DIR}")
        _loaded = True
        return _compiled_domain_rules

    # Load all JSON files in the rules directory
    json_files = sorted(_RULES_DIR.glob("*.json"))

    for filepath in json_files:
        rules = _compile_rules_from_file(filepath)
        _compiled_domain_rules.extend(rules)

    logger.info(
        f"Total domain rules loaded: {len(_compiled_domain_rules)} "
        f"from {len(json_files)} file(s)"
    )
    _loaded = True
    return _compiled_domain_rules


def match_domain_rules(
    inferred_domain: str,
    normalized_label: str,
) -> DomainRule | None:
    """
    Find the first matching domain-scoped rule for a given domain + label.

    Args:
        inferred_domain: The SDTM domain inferred for this field's form.
        normalized_label: The field label, normalized for matching.

    Returns:
        The matching DomainRule, or None if no match found.
    """
    rules = load_all_domain_rules()
    domain_upper = inferred_domain.upper()

    for rule in rules:
        if rule.domain != domain_upper:
            continue
        if rule.label_pattern.search(normalized_label):
            return rule

    return None


def get_rules_for_domain(domain: str) -> list[DomainRule]:
    """Get all loaded rules for a specific domain."""
    rules = load_all_domain_rules()
    domain_upper = domain.upper()
    return [r for r in rules if r.domain == domain_upper]