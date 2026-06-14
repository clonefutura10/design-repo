"""
Tier 3 LLM Resolution — Semantic fallback for unresolved fields.

This module runs ONLY on fields that passed through all deterministic tiers
(Tier 0 passes 0–4.5, Tier 1 NOT SUBMITTED) without a match. It sends the
field label + form context to Claude and asks for an SDTM variable mapping.

Activation:
    Set env var  ANTHROPIC_API_KEY=<your_key>  to enable.
    If the key is absent this module is a no-op and resolve() returns None.

Cost control:
    - Only called on truly unresolved fields (not on every field)
    - Results are cached in cache/llm_resolution_cache.json
    - Max tokens per call: 256 (mapping response is short)
    - Rate-limit: configurable via LLM_MAX_RPS env var (default 5)

Tuning:
    LLM_MODEL      — model id (default: claude-haiku-4-5-20251001)
    LLM_MAX_TOKENS — max tokens per response (default: 256)
    LLM_TEMP       — temperature (default: 0.0 for deterministic output)
    LLM_MAX_RPS    — max requests per second (default: 5)
"""

from __future__ import annotations
import json
import os
import re
import time
import threading
from pathlib import Path

from src.resolution.models import ResolutionResult, ResolutionTier
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration (all overridable via env vars)
# ─────────────────────────────────────────────────────────────────────────────
_API_KEY        = os.getenv("ANTHROPIC_API_KEY", "")
_MODEL          = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")
_MAX_TOKENS     = int(os.getenv("LLM_MAX_TOKENS", "256"))
_TEMPERATURE    = float(os.getenv("LLM_TEMP", "0.0"))
_MAX_RPS        = float(os.getenv("LLM_MAX_RPS", "5"))
_CACHE_FILE     = Path(os.getenv("LLM_CACHE_FILE", "cache/llm_resolution_cache.json"))
_CONFIDENCE     = 0.75   # Confidence assigned to LLM-resolved fields
_MIN_INTERVAL   = 1.0 / _MAX_RPS

# ─────────────────────────────────────────────────────────────────────────────
# Persistent cache — avoids re-calling the API for the same field
# ─────────────────────────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
_last_call_time = 0.0


def _load_cache() -> None:
    global _cache
    if _CACHE_FILE.exists():
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                _cache = json.load(f)
            logger.info(f"LLM cache loaded: {len(_cache)} entries")
        except Exception as exc:
            logger.warning(f"LLM cache load failed: {exc}")
            _cache = {}


def _save_cache() -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning(f"LLM cache save failed: {exc}")


_load_cache()


# ─────────────────────────────────────────────────────────────────────────────
# Prompt template
# ─────────────────────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are an expert in CDISC SDTM (Study Data Tabulation Model).
Given a CRF field label and its form context, respond with the single best SDTM
mapping in strict JSON. If the field should not be submitted, use NOT_SUBMITTED.

Response format (JSON only, no prose):
{
  "domain": "VS",
  "variable": "VSORRES",
  "codelist": "C66770",
  "is_supp": false,
  "confidence": 0.82,
  "rationale": "one-sentence explanation"
}

Rules:
- domain and variable must follow CDISC SDTM IG 3.4 naming conventions
- is_supp: true only for SUPPXX dataset variables
- confidence: your own estimate 0.0–1.0
- If uncertain beyond 0.60, set domain="" and variable="UNRESOLVED"
"""


def _build_user_prompt(form_code: str, form_name: str, field_label: str) -> str:
    lines = [f"Form code: {form_code}"]
    if form_name:
        lines.append(f"Form name: {form_name}")
    lines.append(f"Field label: {field_label}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiter
# ─────────────────────────────────────────────────────────────────────────────
def _rate_limit() -> None:
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_call_time = time.monotonic()


# ─────────────────────────────────────────────────────────────────────────────
# API call
# ─────────────────────────────────────────────────────────────────────────────
def _call_api(form_code: str, form_name: str, field_label: str) -> dict | None:
    """Call the Claude API and return a parsed mapping dict, or None on failure."""
    try:
        import anthropic  # lazy import — not required if key is absent
    except ImportError:
        logger.warning("anthropic package not installed — pip install anthropic")
        return None

    _rate_limit()
    client = anthropic.Anthropic(api_key=_API_KEY)

    try:
        message = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(form_code, form_name, field_label)}],
        )
        text = message.content[0].text.strip()

        # Extract JSON (the model may wrap it in ```json ... ```)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            logger.warning(f"LLM returned no JSON for {form_code}/{field_label}")
            return None

        parsed = json.loads(m.group(0))
        return parsed

    except Exception as exc:
        logger.warning(f"LLM API call failed for {form_code}/{field_label}: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public resolver
# ─────────────────────────────────────────────────────────────────────────────

class Tier3LLM:
    """LLM-based fallback resolver. No-op when ANTHROPIC_API_KEY is not set."""

    @property
    def enabled(self) -> bool:
        return bool(_API_KEY)

    def resolve(
        self,
        form_code: str,
        field_label: str,
        form_name: str = "",
    ) -> ResolutionResult | None:
        """
        Attempt to resolve field_label using the LLM.

        Returns ResolutionResult on success, None if disabled or no clear mapping.
        Results are cached so the same field is never sent to the API twice.
        """
        if not self.enabled:
            return None
        if not form_code or not field_label:
            return None

        cache_key = f"{form_code.upper()}|{field_label.lower().strip()}"

        with _cache_lock:
            if cache_key in _cache:
                cached = _cache[cache_key]
                logger.debug(f"LLM cache hit: {cache_key}")
                return self._build_result(form_code, field_label, cached)

        mapping = _call_api(form_code, form_name, field_label)
        if not mapping:
            return None

        domain   = mapping.get("domain", "").upper()
        variable = mapping.get("variable", "").upper()

        if not domain or not variable or variable in ("", "UNRESOLVED"):
            return None

        llm_confidence = float(mapping.get("confidence", _CONFIDENCE))
        # Cap to the tier's max so it never outranks deterministic passes
        llm_confidence = min(llm_confidence, _CONFIDENCE)

        entry = {
            "domain":    domain,
            "variable":  variable,
            "codelist":  mapping.get("codelist", ""),
            "is_supp":   bool(mapping.get("is_supp", False)),
            "confidence": llm_confidence,
            "rationale": mapping.get("rationale", ""),
        }

        with _cache_lock:
            _cache[cache_key] = entry
            _save_cache()

        logger.info(
            f"LLM resolved {form_code}/{field_label!r} → "
            f"{domain}.{variable} (conf={llm_confidence:.2f})"
        )
        return self._build_result(form_code, field_label, entry)

    @staticmethod
    def _build_result(form_code: str, field_label: str, entry: dict) -> ResolutionResult | None:
        domain   = entry.get("domain", "")
        variable = entry.get("variable", "")
        if not domain or not variable:
            return None

        if variable == "NOT_SUBMITTED":
            return ResolutionResult(
                form_code=form_code,
                field_label=field_label,
                resolved=True,
                tier=ResolutionTier.TIER3_LLM,
                confidence=entry.get("confidence", _CONFIDENCE),
                sdtm_domain="",
                sdtm_variable="NOT SUBMITTED",
                sdtm_label="",
                core="",
                is_supplemental=False,
                is_not_submitted=True,
                codelist_code="",
            )

        return ResolutionResult(
            form_code=form_code,
            field_label=field_label,
            resolved=True,
            tier=ResolutionTier.TIER3_LLM,
            confidence=entry.get("confidence", _CONFIDENCE),
            sdtm_domain=domain,
            sdtm_variable=variable,
            sdtm_label="",
            core="",
            is_supplemental=entry.get("is_supp", False),
            is_not_submitted=False,
            codelist_code=entry.get("codelist", ""),
        )
