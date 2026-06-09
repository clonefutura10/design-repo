"""
Tier 1 - NOT SUBMITTED Classification.

ONLY for fields explicitly marked as not for regulatory submission:
- RSG (Remote Site Gateway) internal fields
- Calculation/derived fields created for system purposes
- Fields with explicit "do not submit" markers
- CRF workflow-only instructions that slip through the parser filter

This does NOT catch dictionary-derived fields (MedDRA, ATC, etc.)
-- those have real SDTM variable mappings and are handled by Tier 0.
"""

from __future__ import annotations
import re
import json
from pathlib import Path

from src.resolution.models import ResolutionResult, ResolutionTier
from src.utils.text_normalizer import normalize_label_for_lookup


# Load dictionary-derived NOT SUBMITTED labels from cache
_NOT_SUBMITTED_LABELS: set[str] = set()
_NOT_SUBMITTED_CACHE = Path("cache/sdtm_not_submitted_labels.json")
if _NOT_SUBMITTED_CACHE.exists():
    try:
        with open(_NOT_SUBMITTED_CACHE, "r", encoding="utf-8") as _f:
            for entry in json.load(_f):
                if "label_normalized" in entry:
                    _NOT_SUBMITTED_LABELS.add(entry["label_normalized"])
    except Exception:
        pass


# Substring patterns (matched against normalized lowercase label)
_CONTAINS_PATTERNS: list[str] = [
    "field created for rsg",
    "field created for calculation",
    "internal use only",
    "do not submit",
    "not for submission",
    "not submitted",
    "not collected",
    "initial email sent",
    "date of birth will be integrated",
    "visit date will be integrated",
]

# Regex patterns (matched against original label for case-sensitivity)
_REGEX_PATTERNS: list[re.Pattern] = [
    re.compile(r"field.*call\s*cf", re.IGNORECASE),
    re.compile(r"^derived\s*field", re.IGNORECASE),
    # CRF workflow instructions that may slip through field_identifier's filter
    re.compile(r"select\s*['\u2018\u2019]yes['\u2018\u2019]\s*to\s*populate", re.IGNORECASE),
]


class Tier1NotSubmitted:
    """Deterministic NOT SUBMITTED classifier - only for truly unmapped fields."""

    def resolve(self, field_label: str, form_code: str = "") -> ResolutionResult | None:
        """
        Check if a field should be classified as NOT SUBMITTED.
        Returns ResolutionResult if matched, None otherwise.
        """
        norm_label = normalize_label_for_lookup(field_label)
        if not norm_label:
            return None

        # Check dictionary-derived NOT SUBMITTED labels (exact normalized match)
        if norm_label in _NOT_SUBMITTED_LABELS:
            return self._build_result(form_code, field_label, "dictionary_derived")

        # Check substring patterns against normalized label
        for pattern in _CONTAINS_PATTERNS:
            if pattern in norm_label:
                return self._build_result(form_code, field_label, f"contains: {pattern}")

        # Check regex patterns against original label
        for regex in _REGEX_PATTERNS:
            if regex.search(field_label):
                return self._build_result(form_code, field_label, f"regex: {regex.pattern}")

        return None

    def _build_result(self, form_code: str, field_label: str, reason: str) -> ResolutionResult:
        """Build NOT SUBMITTED result."""
        return ResolutionResult(
            form_code=form_code,
            field_label=field_label,
            resolved=True,
            tier=ResolutionTier.TIER1_NOT_SUBMITTED,
            confidence=1.0,
            sdtm_domain="",
            sdtm_variable="",
            is_not_submitted=True,
            not_submitted_reason=reason,
        )