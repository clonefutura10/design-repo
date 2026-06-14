"""
Rule Loader — Loads SDTM mapping rules from JSON configuration files.

Rules live in config/rules/*.json. Three file types are supported:
  - form_rules.json    — form-scoped rules (form_pattern + label_pattern)
  - gating_rules.json — universal NOT_SUBMITTED gating questions (any form)
  - universal_rules.json — domain-scoped rules without a form constraint

To add rules for a new therapeutic area or form type, add or edit a JSON
file in config/rules/ — no Python code changes needed.

Schema v2.0 fields per rule:
  form_pattern   (optional str)  — regex matched against the CRF form code
  label_pattern  (required str)  — regex matched against the normalized field label
  domain         (required str)  — target SDTM domain (e.g. "LB")
  variable       (required str)  — target SDTM variable (e.g. "LBORRES")
  codelist       (optional str)  — controlled terminology code (or "")
  is_supp        (optional bool) — True if this maps to SUPPxx dataset
  note           (optional str)  — human-readable description
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_RULES_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "rules"

# Load order matters — form_rules must come before universal_rules so more
# specific (form-scoped) matches win when both could apply.
_LOAD_ORDER = ["form_rules.json", "gating_rules.json", "universal_rules.json"]


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MappingRule:
    """A compiled, optionally form-scoped SDTM mapping rule."""
    domain: str                       # Target SDTM domain
    label_pattern: re.Pattern         # Compiled regex for field label
    variable: str                     # Target SDTM variable
    codelist: str                     # Controlled terminology code (or "")
    is_supp: bool                     # True → maps to SUPPxx dataset
    note: str                         # Human-readable description
    form_pattern: re.Pattern | None   # If set, rule only fires for matching form codes


# ─────────────────────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────────────────────

_all_rules: list[MappingRule] = []
_loaded = False


def _compile_file(filepath: Path) -> list[MappingRule]:
    if not filepath.exists():
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    out: list[MappingRule] = []
    for i, entry in enumerate(data.get("rules", [])):
        try:
            fp_raw = entry.get("form_pattern", "")
            rule = MappingRule(
                domain=entry["domain"].upper(),
                label_pattern=re.compile(entry["label_pattern"], re.IGNORECASE),
                variable=entry["variable"].upper(),
                codelist=entry.get("codelist", ""),
                is_supp=bool(entry.get("is_supp", False)),
                note=entry.get("note", ""),
                form_pattern=re.compile(fp_raw, re.IGNORECASE) if fp_raw else None,
            )
            out.append(rule)
        except (KeyError, re.error) as exc:
            logger.warning(f"Skipping rule #{i} in {filepath.name}: {exc}")

    logger.info(f"Loaded {len(out)} rules from {filepath.name}")
    return out


def _load_all() -> list[MappingRule]:
    global _all_rules, _loaded
    if _loaded:
        return _all_rules

    _all_rules = []
    if not _RULES_DIR.exists():
        _RULES_DIR.mkdir(parents=True, exist_ok=True)
        _loaded = True
        return _all_rules

    # Load in prescribed order first, then any remaining files alphabetically
    loaded_names: set[str] = set()
    for name in _LOAD_ORDER:
        fp = _RULES_DIR / name
        if fp.exists():
            _all_rules.extend(_compile_file(fp))
            loaded_names.add(name)

    for fp in sorted(_RULES_DIR.glob("*.json")):
        if fp.name not in loaded_names:
            _all_rules.extend(_compile_file(fp))

    logger.info(f"Total rules loaded: {len(_all_rules)}")
    _loaded = True
    return _all_rules


def reload_rules() -> None:
    """Force a reload (useful after editing JSON files at runtime)."""
    global _loaded
    _loaded = False
    _load_all()


# ─────────────────────────────────────────────────────────────────────────────
# Public matching API
# ─────────────────────────────────────────────────────────────────────────────

def match_form_rules(form_code: str, normalized_label: str) -> MappingRule | None:
    """
    Find the first rule where the form_pattern matches form_code AND the
    label_pattern matches normalized_label.

    Rules without a form_pattern are skipped here — use match_domain_rules()
    for domain-only matching.
    """
    rules = _load_all()
    for rule in rules:
        if rule.form_pattern is None:
            continue
        if not rule.form_pattern.match(form_code):
            continue
        if rule.label_pattern.search(normalized_label):
            return rule
    return None


def match_domain_rules(inferred_domain: str, normalized_label: str) -> MappingRule | None:
    """
    Find the first rule where:
      - form_pattern is absent (domain-scoped only), AND
      - domain matches inferred_domain, AND
      - label_pattern matches normalized_label.
    """
    rules = _load_all()
    domain_upper = inferred_domain.upper()
    for rule in rules:
        if rule.form_pattern is not None:
            continue
        if rule.domain != domain_upper:
            continue
        if rule.label_pattern.search(normalized_label):
            return rule
    return None


def match_gating_rules(normalized_label: str) -> MappingRule | None:
    """
    Check universal gating rules (form_pattern = '.*') against any label.
    Returns the first match or None.
    """
    rules = _load_all()
    for rule in rules:
        if rule.form_pattern is None:
            continue
        # Gating rules have form_pattern=".*" — they match any form code
        if rule.form_pattern.pattern != ".*":
            continue
        if rule.label_pattern.search(normalized_label):
            return rule
    return None


def get_rules_for_domain(domain: str) -> list[MappingRule]:
    """All loaded rules (any type) for a specific SDTM domain."""
    domain_upper = domain.upper()
    return [r for r in _load_all() if r.domain == domain_upper]
