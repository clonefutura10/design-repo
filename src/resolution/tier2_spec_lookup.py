"""
Tier 2 — Corporate Specification Lookup.

Resolves CRF fields by matching against az_spec_lookup.json using
progressive string matching:
  1. Exact normalized match (confidence 0.95)
  2. Cleaned match — remove noise words (confidence 0.88)
  3. High token overlap — Jaccard > 0.75 (confidence 0.78)
  4. Substring containment (confidence 0.72)

This replaces the failed FAISS-based approach with deterministic string
operations that are transparent, debuggable, and fast.
"""

from __future__ import annotations
import json
import re
from pathlib import Path
from functools import lru_cache

from src.resolution.models import ResolutionResult, ResolutionTier
from src.resolution.form_module_mapper import get_valid_spec_modules_for_form
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# NOISE WORDS — removed during "cleaned" matching phase
# These appear in CRF labels but not in spec labels
# ═══════════════════════════════════════════════════════════════════════

_NOISE_WORDS: set[str] = {
    "please", "specify", "select", "enter", "record", "indicate",
    "the", "a", "an", "of", "for", "is", "was", "were", "are",
    "this", "that", "if", "or", "and", "to", "in", "on", "at",
    "yes", "no", "subject", "patient", "participant",
}

_NOISE_PREFIXES: list[str] = [
    "was the ",
    "is the ",
    "were the ",
    "did the ",
    "does the ",
    "has the ",
    "have the ",
    "please specify ",
    "please enter ",
    "specify ",
    "select ",
    "enter ",
    "record ",
    "indicate ",
]


def _normalize(text: str) -> str:
    """Basic normalization: lowercase, collapse whitespace, strip."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.lower().strip())


def _clean_label(label: str) -> str:
    """
    Aggressive cleaning: remove noise prefixes/suffixes and noise words.
    Used for fuzzy matching when exact fails.
    """
    cleaned = _normalize(label)

    # Remove common CRF prefixes
    for prefix in _NOISE_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break

    # Remove trailing question mark
    cleaned = cleaned.rstrip("?").strip()

    # Remove noise words (but keep content words)
    tokens = cleaned.split()
    content_tokens = [t for t in tokens if t not in _NOISE_WORDS and len(t) > 1]

    return " ".join(content_tokens) if content_tokens else cleaned


def _tokenize(text: str) -> set[str]:
    """Split into word tokens for Jaccard comparison."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard index between two token sets."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _weighted_token_overlap(crf_tokens: set[str], spec_tokens: set[str]) -> float:
    """
    Weighted overlap: what fraction of CRF tokens appear in spec label?
    This is directional — we care that the CRF field's content words
    are present in the spec entry, not vice versa.
    """
    if not crf_tokens:
        return 0.0
    overlap = len(crf_tokens & spec_tokens)
    return overlap / len(crf_tokens)


class Tier2SpecLookup:
    """
    Corporate specification lookup using progressive string matching.

    Loads az_spec_lookup.json once and provides fast lookups by module.
    """

    def __init__(self, spec_path: str | Path = "cache/az_spec_lookup.json"):
        self._spec_path = Path(spec_path)
        self._data: dict[str, dict[str, list[dict]]] = {}
        self._loaded = False

        # Pre-computed token sets for spec labels (for Jaccard matching)
        # Structure: {module: {normalized_label: token_set}}
        self._spec_tokens: dict[str, dict[str, set[str]]] = {}

    def _ensure_loaded(self):
        """Lazy-load the spec data on first use."""
        if self._loaded:
            return

        if not self._spec_path.exists():
            logger.error(f"Spec lookup file not found: {self._spec_path}")
            self._loaded = True
            return

        logger.info(f"Loading az_spec_lookup.json ({self._spec_path.stat().st_size / 1024 / 1024:.1f} MB)")

        with open(self._spec_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

        # Pre-compute token sets for each label in each module
        for module, labels in self._data.items():
            self._spec_tokens[module] = {}
            for label in labels.keys():
                self._spec_tokens[module][label] = _tokenize(label)

        total_labels = sum(len(labels) for labels in self._data.values())
        logger.info(
            f"Spec lookup loaded: {len(self._data)} modules, "
            f"{total_labels} unique labels"
        )
        self._loaded = True

    def resolve(
        self,
        form_code: str,
        field_label: str,
    ) -> ResolutionResult | None:
        """
        Attempt to resolve a CRF field using the corporate spec lookup.

        Tries progressively looser matching strategies. Returns the first
        match that exceeds the confidence threshold, or None.
        """
        self._ensure_loaded()

        if not form_code or not field_label:
            return None

        # Get modules to search
        modules = get_valid_spec_modules_for_form(form_code, str(self._spec_path))
        if not modules:
            return None

        norm_label = _normalize(field_label)
        if not norm_label or len(norm_label) < 3:
            return None

        # ─── Strategy 1: Exact normalized match ───
        result = self._try_exact_match(modules, norm_label, form_code, field_label)
        if result:
            return result

        # ─── Strategy 2: Cleaned label match ───
        cleaned = _clean_label(field_label)
        if cleaned and cleaned != norm_label:
            result = self._try_exact_match(
                modules, cleaned, form_code, field_label, confidence=0.88
            )
            if result:
                return result

        # ─── Strategy 3: High token overlap (Jaccard ≥ 0.75) ───
        result = self._try_token_overlap(
            modules, norm_label, form_code, field_label,
            min_jaccard=0.75, confidence=0.78
        )
        if result:
            return result

        # ─── Strategy 4: Cleaned token overlap (Jaccard ≥ 0.70) ───
        if cleaned and cleaned != norm_label:
            result = self._try_token_overlap(
                modules, cleaned, form_code, field_label,
                min_jaccard=0.70, confidence=0.72
            )
            if result:
                return result

        return None

    def _try_exact_match(
        self,
        modules: list[str],
        lookup_label: str,
        form_code: str,
        original_label: str,
        confidence: float = 0.95,
    ) -> ResolutionResult | None:
        """Try exact string match in spec labels."""
        for module in modules:
            module_data = self._data.get(module, {})
            if lookup_label in module_data:
                entries = module_data[lookup_label]
                return self._pick_best_entry(
                    entries, form_code, original_label, confidence
                )
        return None

    def _try_token_overlap(
        self,
        modules: list[str],
        label: str,
        form_code: str,
        original_label: str,
        min_jaccard: float = 0.75,
        confidence: float = 0.78,
    ) -> ResolutionResult | None:
        """Try token-based fuzzy matching."""
        query_tokens = _tokenize(label)

        # Skip very short token sets (too ambiguous)
        if len(query_tokens) < 2:
            return None

        best_score = 0.0
        best_entries = None

        for module in modules:
            module_tokens = self._spec_tokens.get(module, {})
            module_data = self._data.get(module, {})

            for spec_label, spec_token_set in module_tokens.items():
                # Skip very short spec labels
                if len(spec_token_set) < 2:
                    continue

                jaccard = _jaccard_similarity(query_tokens, spec_token_set)

                # Also check directional overlap (CRF→spec)
                directional = _weighted_token_overlap(query_tokens, spec_token_set)

                # Combined score: weight both metrics
                combined = (jaccard * 0.6) + (directional * 0.4)

                if combined > best_score and combined >= min_jaccard:
                    best_score = combined
                    best_entries = module_data[spec_label]

        if best_entries:
            # Adjust confidence by match quality
            adjusted_confidence = confidence * min(best_score / min_jaccard, 1.0)
            return self._pick_best_entry(
                best_entries, form_code, original_label, adjusted_confidence
            )

        return None

    def _pick_best_entry(
        self,
        entries: list[dict],
        form_code: str,
        field_label: str,
        confidence: float,
    ) -> ResolutionResult:
        """
        Pick the best mapping entry when multiple exist.

        Priority:
        1. Entries with map_order "1" (primary mapping)
        2. Non-supplemental over supplemental
        3. First entry as fallback
        """
        # Filter to primary mappings (map_order == "1")
        primary = [e for e in entries if e.get("map_order", "") == "1"]
        candidates = primary if primary else entries

        # Prefer non-supplemental
        non_supp = [e for e in candidates if not e.get("is_supplemental", False)]
        chosen = non_supp[0] if non_supp else candidates[0]

        return ResolutionResult(
            form_code=form_code,
            field_label=field_label,
            resolved=True,
            tier=ResolutionTier.TIER2_SPEC_LOOKUP,
            confidence=confidence,
            sdtm_domain=chosen.get("sdtm_domain", ""),
            sdtm_variable=chosen.get("sdtm_variable", ""),
            sdtm_label=chosen.get("sdtm_label", ""),
            core=chosen.get("core", ""),
            is_supplemental=chosen.get("is_supplemental", False),
            codelist_code="",
        )

    def get_all_candidates_for_form(self, form_code: str) -> list[dict]:
        """
        Return ALL spec entries for a form's modules.
        Used to build LLM prompt candidates when Tier 2 fails.
        """
        self._ensure_loaded()

        modules = get_valid_spec_modules_for_form(form_code, str(self._spec_path))
        candidates = []
        seen_keys = set()

        for module in modules:
            module_data = self._data.get(module, {})
            for label, entries in module_data.items():
                for entry in entries:
                    key = f"{entry.get('sdtm_domain', '')}.{entry.get('sdtm_variable', '')}"
                    if key not in seen_keys:
                        candidates.append(entry)
                        seen_keys.add(key)

        return candidates