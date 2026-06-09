"""
Form-to-Module Mapper — Refactored for Cross-Study Portability.

Maps CRF form codes to:
1. RAW module names in az_spec_lookup.json (for Tier 0/2 spec lookup)
2. SDTM domain names (for standards lookup and rule scoping)

Resolution strategy:
  1. Check known overrides (legacy hardcoded mappings that are CORRECT)
  2. Use domain_inferencer for everything else
  3. Build module search list from inferred domains + code variants
"""

from __future__ import annotations
import re
import json
from pathlib import Path
from functools import lru_cache

from src.resolution.domain_inferencer import infer_domains_cached, DomainInference
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SPEC MODULE ALIASES
# Some forms have their data under oddly-named spec modules due to the
# concatenation logic in build_cache.py. These are empirically observed
# and will NOT be found by inference alone.
# ═══════════════════════════════════════════════════════════════════════════════

_SPEC_MODULE_OVERRIDES: dict[str, list[str]] = {
    # Only include mappings that are NOT derivable from domain inference
    "SUTRA": ["SUTRASUTRASUTRADM"],
    "CONSENT": ["CONSENTCONSENTDSDSDSDSSUPPDS", "CONSENTDM", "CONSENTCONSENTCONSENTDM"],
    "EXACATE": ["EXACA", "EXACT"],
    "AZAWSAE": ["SERAE"],
    "HEVENT1": ["HEVENT"],
    "CONSWD1": ["CONSWD"],
}


def get_spec_modules_for_form(
    form_code: str,
    form_name: str = "",
    spec_lookup_path: str = "cache/az_spec_lookup.json",
) -> list[str]:
    """
    Return list of module names to search in az_spec_lookup.json.

    Strategy (ordered by priority):
    1. Form code itself (exact match) — always tried
    2. Base code (strip trailing digits/underscores)
    3. Override aliases (for known concatenated module names)
    4. Inferred domains from domain_inferencer
    5. SUPP variants for all candidates
    
    All candidates are validated against actually available modules.
    """
    if not form_code:
        return []

    fc = form_code.upper().strip()
    base = re.sub(r"\d+$", "", fc)                    # Strip trailing digits
    underscore_prefix = fc.split("_")[0] if "_" in fc else ""  # LB_HEM → LB

    # Collect candidates (order = priority)
    candidates: list[str] = []

    # 1. Exact form code
    candidates.append(fc)

    # 2. Base (trailing digits stripped)
    if base and base != fc:
        candidates.append(base)

    # 3. Underscore prefix
    if underscore_prefix and underscore_prefix != fc and underscore_prefix != base:
        candidates.append(underscore_prefix)

    # 4. Override aliases (for concatenated module names from spec build)
    if fc in _SPEC_MODULE_OVERRIDES:
        candidates.extend(_SPEC_MODULE_OVERRIDES[fc])
    elif base in _SPEC_MODULE_OVERRIDES:
        candidates.extend(_SPEC_MODULE_OVERRIDES[base])

    # 5. Domain inference
    inference = infer_domains_cached(form_code, form_name)
    if inference.domains:
        for domain in inference.domains:
            if domain not in candidates:
                candidates.append(domain)

    # 6. Add SUPP variants for all
    all_modules: list[str] = []
    seen: set[str] = set()
    for m in candidates:
        m_upper = m.upper()
        if m_upper not in seen:
            all_modules.append(m_upper)
            seen.add(m_upper)
        supp = f"SUPP{m_upper}"
        if supp not in seen:
            all_modules.append(supp)
            seen.add(supp)

    return all_modules


def get_sdtm_domains_for_form(
    form_code: str,
    form_name: str = "",
) -> list[str]:
    """
    Return SDTM domain names for a form.
    
    Used for:
    - Scoping standards lookup (sdtm_spec_by_dataset.json)
    - Scoping tier0 rule matching (domain-based fallback)
    - LLM candidate generation
    """
    if not form_code:
        return []

    inference = infer_domains_cached(form_code, form_name)

    if inference.domains:
        # Include SUPP variants
        all_domains: list[str] = []
        for d in inference.domains:
            all_domains.append(d)
            all_domains.append(f"SUPP{d}")
        return all_domains

    # Absolute fallback: use code itself if it looks like a domain
    fc = form_code.upper().strip()
    base = re.sub(r"\d+$", "", fc)
    return [base, f"SUPP{base}"] if base else [fc]


@lru_cache(maxsize=1)
def get_available_spec_modules(spec_lookup_path: str = "cache/az_spec_lookup.json") -> set[str]:
    """Load and cache the set of modules available in az_spec_lookup.json."""
    path = Path(spec_lookup_path)
    if not path.exists():
        logger.warning(f"az_spec_lookup.json not found at {path}")
        return set()

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    modules = set(data.keys())
    logger.info(f"az_spec_lookup.json: {len(modules)} modules available")
    return modules


def get_valid_spec_modules_for_form(
    form_code: str,
    spec_lookup_path: str = "cache/az_spec_lookup.json",
    form_name: str = "",
) -> list[str]:
    """
    Return only modules that actually exist in az_spec_lookup.json.
    This is what Tier 0/2 uses for resolution.
    """
    available = get_available_spec_modules(spec_lookup_path)
    candidate_modules = get_spec_modules_for_form(form_code, form_name, spec_lookup_path)

    valid = [m for m in candidate_modules if m in available]

    if not valid and candidate_modules:
        logger.debug(
            f"Form '{form_code}' ({form_name}): no modules found in spec. "
            f"Tried: {candidate_modules[:8]}"
        )

    return valid