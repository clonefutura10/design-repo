"""
Tier 0 Rules - Deterministic form-aware SDTM mapping.

Resolution order:
    Pass 0: Learned mappings from reference aCRFs -> confidence 0.96
    Pass 1: Hardcoded regex rules (exact form_code match) -> confidence 0.98
    Pass 2: Hardcoded regex rules (inferred domain match) -> confidence 0.95
    Pass 2.5: Universal domain-scoped JSON rules -> confidence 0.93
    Pass 3: SDTM Standards (sdtm_spec_by_dataset.json) -> confidence 0.92
    Pass 4: AZ Spec Lookup (az_spec_lookup.json) -> confidence 0.90

NOTE: When is_supp=True, store the BASE domain only (e.g., "AE" not "SUPPAE").
      The pdf_writer automatically prepends "SUPP" for supplemental fields.

CROSS-STUDY PORTABILITY (v8):
    - Pass 0 enables instant lookup from verified past aCRFs
    - Pass 2 enables rules to fire for unknown form codes via domain inference
    - Pass 2.5 uses external JSON rules matched purely on inferred domain
    - Standards/AZ Spec lookups now use inferred domains as fallback
    - Anti-duplication guard delegated to usage_guard module (findings-aware)
    - Multi-mapping support for fields that map to multiple SDTM variables
    - form_name can be passed for better inference quality
    - Oncology domain guard prevents TR/TU/RS leaking into non-oncology studies
"""

from __future__ import annotations
import re
import json
from pathlib import Path
from collections import defaultdict

from src.resolution.models import ResolutionResult, ResolutionTier
from src.utils.text_normalizer import normalize_label_for_lookup
from src.utils.logging_config import get_logger
from src.resolution.domain_inferencer import infer_domains_cached
from src.resolution.usage_guard import check_usage, reset_usage_tracking
import threading
from src.resolution.rule_loader import match_domain_rules, match_form_rules, match_gating_rules

logger = get_logger(__name__)


# ============================================================================
# CONFIDENCE LEVELS
# ============================================================================
_CONF_LEARNED = 0.96               # Learned from verified reference aCRFs
_CONF_EXACT_FORM_RULE = 0.98       # Rule fired on exact form_code match
_CONF_INFERRED_DOMAIN_RULE = 0.95  # Rule fired via inferred domain
_CONF_DOMAIN_JSON_RULE = 0.93      # Rule fired via JSON domain-scoped rules
_CONF_STANDARDS = 0.92             # SDTM standards match
_CONF_AZ_SPEC = 0.90              # AZ spec lookup match


# ============================================================================
# THERAPEUTIC AREA GUARD
# TR/TU/RS are oncology-only. Block in non-oncology studies.
# ============================================================================
_ONCOLOGY_ONLY_DOMAINS = {"TR", "TU", "RS"}
_thread_local = threading.local()


def _is_oncology_study() -> bool:
    return getattr(_thread_local, 'is_oncology', False)


def _detect_oncology_study(form_codes: set[str]) -> bool:
    """Detect if study is oncology-related based on form codes present."""
    oncology_indicators = {
        "RECIST", "TUMOR", "TUMOUR", "DISEXT", "ONCRSR",
        "PATHGEN", "PATHREP", "CAPRX", "CAPXR", "TARG",
        "NTARG", "NEWLES", "IMGASS", "ECHOC",
    }
    for fc in form_codes:
        fc_upper = fc.upper()
        for indicator in oncology_indicators:
            if indicator in fc_upper:
                return True
    return False


def set_study_context(form_codes: set[str]):
    """Call once at pipeline start with all form codes in the study."""
    _thread_local.is_oncology = _detect_oncology_study(form_codes)
    logger.info(f"Study context: oncology={_thread_local.is_oncology}, forms={len(form_codes)}")


# ============================================================================
# LOAD LEARNED MAPPINGS (from reference aCRFs)
# ============================================================================
_LEARNED_MAPPINGS_FILE = Path("cache/learned_mappings.json")
_LEARNED_MAPPINGS: dict[str, dict] = {}

if _LEARNED_MAPPINGS_FILE.exists():
    try:
        with open(_LEARNED_MAPPINGS_FILE, "r", encoding="utf-8") as f:
            _learned_data = json.load(f)
        _LEARNED_MAPPINGS = _learned_data.get("mappings", {})
        logger.info(f"Loaded learned mappings: {len(_LEARNED_MAPPINGS)} entries")
        del _learned_data
    except Exception as e:
        logger.warning(f"Failed to load learned mappings: {e}")


# ============================================================================
# MULTI-MAPPING TABLE
# For fields that legitimately map to multiple SDTM variables.
# Key: (primary_domain, primary_variable)
# Value: list of additional mappings with optional form/label conditions
# ============================================================================
_MULTI_MAP_TABLE: dict[tuple[str, str], list[dict]] = {
    # ── Disposition dates ────────────────────────────────────────────────────
    # Informed consent date → DS.DSSTDTC + DM.RFICDTC
    ("DS", "DSSTDTC"): [
        {
            "domain": "DM",
            "variable": "RFICDTC",
            "label": "Date/Time of Informed Consent",
            "condition_form": r"CONSENT|ICF|DS_ICF",
            "condition_label": r"informed\s*consent",
        },
        {
            "domain": "DM",
            "variable": "RFSTDTC",
            "label": "Subject Reference Start Date/Time",
            "condition_label": r"(first\s*dose|start\s*of\s*treat|date\s*of\s*randomiz)",
        },
        {
            "domain": "DM",
            "variable": "RFENDTC",
            "label": "Subject Reference End Date/Time",
            "condition_label": r"(end\s*of\s*treat|last\s*treat|withdrawal\s*date|study\s*end)",
        },
        {
            "domain": "DM",
            "variable": "RFPENDTC",
            "label": "Date/Time of End of Participation",
            "condition_label": r"(end\s*of\s*(study\s*)?participation|last.*follow.?up)",
        },
        {
            "domain": "DM",
            "variable": "DTHDTC",
            "label": "Date/Time of Death",
            "condition_label": r"(death|died|date\s*of\s*death)",
        },
    ],

    # ── Exposure dates ───────────────────────────────────────────────────────
    # First dose → EX.EXSTDTC + DM.RFXSTDTC
    ("EX", "EXSTDTC"): [
        {
            "domain": "DM",
            "variable": "RFXSTDTC",
            "label": "Date/Time of First Study Treatment",
            "condition_label": r"first\s*(dose|administration|study\s*treat)",
        },
    ],
    # Last dose → EX.EXENDTC + DM.RFXENDTC
    ("EX", "EXENDTC"): [
        {
            "domain": "DM",
            "variable": "RFXENDTC",
            "label": "Date/Time of Last Study Treatment",
            "condition_label": r"last\s*(dose|administration|study\s*treat)",
        },
    ],

    # ── Death details ────────────────────────────────────────────────────────
    # DD.DDDTC → always also DM.DTHDTC (death date is always both)
    ("DD", "DDDTC"): [
        {
            "domain": "DM",
            "variable": "DTHDTC",
            "label": "Date/Time of Death",
        },
        {
            "domain": "DS",
            "variable": "DSSTDTC",
            "label": "Date/Time of Collection",
        },
    ],

    # ── Adverse event dates ──────────────────────────────────────────────────
    # AE onset date → AE.AESTDTC; if SAE hospitalization, also CE.CESTDTC
    ("AE", "AESTDTC"): [
        {
            "domain": "CE",
            "variable": "CESTDTC",
            "label": "Start Date/Time of Clinical Event",
            "condition_label": r"(hospitaliz|admiss|inpatient)",
        },
    ],

    # ── Findings: ORRES always also maps to STRESC ───────────────────────────
    ("VS", "VSORRES"): [
        {"domain": "VS", "variable": "VSSTRESC", "label": "Character Result/Finding in Std Format"},
    ],
    ("LB", "LBORRES"): [
        {"domain": "LB", "variable": "LBSTRESC", "label": "Character Result/Finding in Std Format"},
    ],
    ("EG", "EGORRES"): [
        {"domain": "EG", "variable": "EGSTRESC", "label": "Character Result/Finding in Std Format"},
    ],

    # ── EC/EX dual-domain: each EC field also maps to EX ───────────────────
    ("EC", "ECSTDTC"): [
        {"domain": "EX", "variable": "EXSTDTC", "label": "Start Date/Time of Treatment"},
        {"domain": "DM", "variable": "RFXSTDTC", "label": "Date/Time of First Study Treatment",
         "condition_label": r"first\s*(dose|administration|study\s*treat)"},
    ],
    ("EC", "ECENDTC"): [
        {"domain": "EX", "variable": "EXENDTC", "label": "End Date/Time of Treatment"},
        {"domain": "DM", "variable": "RFXENDTC", "label": "Date/Time of Last Study Treatment",
         "condition_label": r"last\s*(dose|administration|study\s*treat)"},
    ],
    ("EC", "ECLOC"): [
        {"domain": "EX", "variable": "EXLOC", "label": "Location of Dose Administration"},
    ],
    ("EC", "ECLAT"): [
        {"domain": "EX", "variable": "EXLAT", "label": "Laterality"},
    ],
    ("EC", "ECACN"): [
        {"domain": "EX", "variable": "EXADJ", "label": "Reason for Dose Adjustment"},
    ],
    ("EC", "ECDOSE"): [
        {"domain": "EX", "variable": "EXDOSE", "label": "Dose per Administration"},
    ],
    ("EC", "ECDOSU"): [
        {"domain": "EX", "variable": "EXDOSU", "label": "Dose Units"},
    ],

    # ── SERAE death date also maps to DM.DTHDTC ─────────────────────────────
    ("AE", "AEDTHDT"): [
        {"domain": "DM", "variable": "DTHDTC", "label": "Date/Time of Death"},
    ],

    # ── Concomitant medication ───────────────────────────────────────────────
    # Indication also maps to MH.MHTERM when referencing pre-existing condition
    ("CM", "CMINDC"): [
        {
            "domain": "MH",
            "variable": "MHTERM",
            "label": "Medical History Verbatim Term",
            "condition_label": r"(indication|reason\s*for|underlying\s*condition|prior\s*condition)",
        },
    ],
}




# ============================================================================
# LOAD SDTM STANDARDS
# ============================================================================
_STANDARDS_FILE = Path("cache/sdtm_spec_by_dataset.json")
_STANDARDS_INDEX: dict[str, dict[str, list[dict]]] = {}

if _STANDARDS_FILE.exists():
    try:
        with open(_STANDARDS_FILE, "r", encoding="utf-8") as f:
            _raw_standards = json.load(f)
        for dataset, entries in _raw_standards.items():
            _STANDARDS_INDEX[dataset] = defaultdict(list)
            for entry in entries:
                label_norm = entry.get("label_normalized", "")
                if label_norm:
                    _STANDARDS_INDEX[dataset][label_norm].append(entry)
        total_labels = sum(len(v) for v in _STANDARDS_INDEX.values())
        logger.info(f"Loaded SDTM Standards: {total_labels} labels across {len(_STANDARDS_INDEX)} datasets")
        del _raw_standards
    except Exception as e:
        logger.warning(f"Failed to load SDTM Standards: {e}")


# ============================================================================
# LOAD AZ SPEC LOOKUP + pre-build fuzzy token index
# ============================================================================
_AZ_SPEC_FILE = Path("cache/az_spec_lookup.json")
_AZ_SPEC_LOOKUP: dict[str, dict[str, list[dict]]] = {}
# Pre-tokenised sets for Jaccard matching: {module: {norm_label: frozenset}}
_AZ_SPEC_TOKENS: dict[str, dict[str, frozenset[str]]] = {}

if _AZ_SPEC_FILE.exists():
    try:
        with open(_AZ_SPEC_FILE, "r", encoding="utf-8") as f:
            _AZ_SPEC_LOOKUP = json.load(f)
        for _mod, _labels in _AZ_SPEC_LOOKUP.items():
            _AZ_SPEC_TOKENS[_mod] = {
                _lbl: frozenset(re.findall(r"[a-z0-9]+", _lbl.lower()))
                for _lbl in _labels
            }
        total_modules = len(_AZ_SPEC_LOOKUP)
        total_entries = sum(len(v) for v in _AZ_SPEC_LOOKUP.values())
        logger.info(f"Loaded AZ Spec Lookup: {total_entries} labels across {total_modules} modules")
    except Exception as e:
        logger.warning(f"Failed to load AZ Spec Lookup: {e}")


# ============================================================================
# FORM -> DOMAIN MAP (duplicates removed — each key appears exactly once)
# ============================================================================
_FORM_TO_DOMAIN: dict[str, str] = {
    "AE": "AE", "SERAE": "AE", "AZAWSAE": "AE", "AELOG": "AE",
    "CM": "CM", "CM1": "CM", "CMLOG": "CM", "CM1LOG": "CM",
    "MH": "MH", "HISM": "MH",
    "VS": "VS", "VS1": "VS",
    "EG": "EG",
    "DM": "DM", "DEM": "DM",
    "PE": "PR", "PE1": "PR", "PE2": "PR", "PHYS": "PR", "PHYSF": "PR",
    "HISS": "PR",
    "LB": "LB", "LB1": "LB", "LB2": "LB", "LB3": "LB",
    "LB_HEM": "LB", "LB_CHEM": "LB", "LB_URIN": "LB", "LB_COAG": "LB",
    "PREG": "LB",
    "SU_NIC": "SU", "SU": "SU", "SUNIC": "SU", "SU_ALC": "SU", "SUALC": "SU",
    "ALLERH": "MH",
    "CONSENT": "DS", "CONSENT1": "DS", "CONSENT2": "DS",
    "DS_ICF": "DS", "DS_RICF": "DS", "DS_WICF": "DS", "DS_EOS": "DS",
    "CONSWD": "DS", "CONSWD1": "DS", "ICFGEN": "DS",
    "DOSDISC": "DS", "PARTOPT": "DS",
    "IE": "IE", "IE1": "IE", "CRIT": "IE",
    "VISIT": "SV", "VISIT1": "SV", "VISIT2": "SV", "VISIT3": "SV",
    "VISITP": "SV", "CONTACT": "SV", "UNS_VIS": "SV",
    "SV": "SV",
    "DS": "DS", "DS1": "DS",
    "RESHISTE": "MH",
    "HEVENT": "FAHO", "HEVENT1": "FAHO",
    "CHCSS": "HO",
    "PULMTE": "FA",
    "EXACD": "CE", "EXACD1": "CE", "EXACATE": "CE", "PR_CE": "CE",
    "CE": "CE",
    "HELMINTH": "FA",
    "INFDI": "FA", "INFRF": "FA",
    "INFSS": "CE",
    "LIVERSS": "CE", "LIVERDI": "FA",
    "LIVERRF": "CO",
    "OVERDOSE": "EC", "EXP": "EC",
    "PREGREP": "RP", "RP": "RP",
    "ASMPERF": "PR", "ASMPERF1": "PR", "ASMPERF2": "PR",
    "EX": "EX", "EX1": "EX",
    "SPCBEDB": "BE", "SPCBEDB1": "BE",
    "SPCPKB": "PC", "SPCPKB1": "IS",
    "SPCGIB": "BE", "SPCGIB1": "BE",
    "SUTRA": "DS",
    "PR": "PR", "PR_DIAG": "PR", "PR_HRCT": "PR",
    "PR1": "PR", "PR1LOG": "PR",
    "DD": "DD",
    "SC": "SC",
    "RE": "RE", "RE_FENO": "RE",
    "EDS": "BE", "EDS1": "BE",
    "HRU": "HO",
    "UNS": "PR",
    # Oncology forms
    "CAPRX": "CM", "CAPXROM": "PR",
    "PATHGEN": "FA", "PATHREP": "FA",
    "DISEXT": "TU",
    "GROUP": "DM", "ENROL": "DM",
    "BOXRAY": "PR", "ECHOC": "PR",
    "HPV": "LB",
    # Questionnaires (QS) — validated instruments, PROs, scoring tools
    "QS": "QS", "PSTAT": "QS", "ECOG": "QS",
    "CGIC": "QS", "PGIC": "QS", "PGIS": "QS",
    "BSI": "QS", "BHQ": "QS", "BVAS": "QS", "BVASV3": "QS",
    "ACQ": "QS", "ACQ5": "QS", "ACQ6": "QS", "ACQ7": "QS",
    "AQLQ": "QS", "AQLQS": "QS",
    "DAPSA": "QS", "DLQI": "QS",
    "EQ5D": "QS", "EQ5D5L": "QS", "EUROQOL": "QS",
    "GAD7": "QS", "PHQ9": "QS",
    "HAQ": "QS", "HAQDI": "QS",
    "MMRC": "QS", "MMRCD": "QS", "CAT": "QS",
    "NRS": "QS", "VAS": "QS", "NPRS": "QS",
    "PASI": "QS", "IGA": "QS",
    "PROMIS": "QS", "WPAI": "QS",
    "QOL": "QS", "QOLB": "QS", "QOLBRSS": "QS",
    "SGRQ": "QS", "SNOT22": "QS",
    "SF36": "QS", "SF12": "QS",
    "SCORAD": "QS", "EASI": "QS",
    "FACT": "QS", "FACIT": "QS",
    "FLIE": "QS", "MDASI": "QS",
    "KCCQ": "QS", "NYHA": "QS",
    "WOMAC": "QS", "BASDAI": "QS",
    "ESAS": "QS", "BPI": "QS",
    "FOSQ": "QS", "ESS": "QS",
    "MNA": "QS", "MOCA": "QS", "MMSE": "QS",
}

_FORM_MAP_FILE = Path("cache/form_to_domain_map.json")
if _FORM_MAP_FILE.exists():
    try:
        with open(_FORM_MAP_FILE, "r", encoding="utf-8") as f:
            _FORM_TO_DOMAIN.update(json.load(f))
    except Exception:
        pass


# ============================================================================
# QUESTIONNAIRE HEURISTIC
# ============================================================================

def _is_questionnaire_form(form_code: str, field_labels: list[str] = None) -> bool:
    """
    Heuristic: detect if a form is likely a questionnaire/PRO instrument.
    Used as fallback when form_code isn't in _FORM_TO_DOMAIN.
    """
    form_upper = form_code.upper().strip()

    # Check explicit mapping first
    if _FORM_TO_DOMAIN.get(form_upper) == "QS":
        return True

    # Keyword patterns in form code
    qs_keywords = (
        "SCORE", "SCALE", "INDEX", "QUESTIONNAIRE", "PRO",
        "SURVEY", "ASSESS", "RATING", "INVENTORY",
    )
    for kw in qs_keywords:
        if kw in form_upper:
            return True

    return False


# ============================================================================
# DOMAIN INFERENCE FOR FORM CODE
# ============================================================================

def _get_domain_for_form(form_code: str) -> str:
    """Get primary domain for a form code using legacy map + inferencer fallback."""
    form_upper = form_code.upper().strip()

    if form_upper in _FORM_TO_DOMAIN:
        return _FORM_TO_DOMAIN[form_upper]

    base = re.sub(r"\d+$", "", form_upper)
    if base in _FORM_TO_DOMAIN:
        return _FORM_TO_DOMAIN[base]

    if form_upper.startswith("SPC"):
        if any(form_upper.startswith(p) for p in ("SPCBEDB", "SPCGIB")):
            return "BE"
        if form_upper.startswith("SPCPKB1"):
            return "IS"
        return "PC"

    # Questionnaire heuristic fallback
    if _is_questionnaire_form(form_code):
        return "QS"

    inference = infer_domains_cached(form_code, "")
    if inference.domains and inference.confidence >= 0.70:
        return inference.primary_domain

    return ""


# ============================================================================
# ANTI-DUPLICATION GUARD
# ============================================================================

def _reset_usage_tracking():
    """Call between pipeline runs to reset the guard."""
    reset_usage_tracking()


# ============================================================================
# HELPERS
# ============================================================================
_STRIP_TRAILING_DIGITS = re.compile(r"\s+\d+$")
_STRIP_PARENS = re.compile(r"\s*\(.*?\)\s*$")

# Noise words removed during fuzzy label cleaning (mirrors former tier2 logic)
_FUZZY_NOISE_WORDS: frozenset[str] = frozenset({
    "please", "specify", "select", "enter", "record", "indicate",
    "the", "a", "an", "of", "for", "is", "was", "were", "are",
    "this", "that", "if", "or", "and", "to", "in", "on", "at",
    "yes", "no", "subject", "patient", "participant",
})
_FUZZY_NOISE_PREFIXES: tuple[str, ...] = (
    "was the ", "is the ", "were the ", "did the ", "does the ",
    "has the ", "have the ", "please specify ", "please enter ",
    "specify ", "select ", "enter ", "record ", "indicate ",
)


def _fuzzy_clean(label: str) -> str:
    """Strip CRF preamble and noise words for fuzzy matching."""
    cleaned = re.sub(r"\s+", " ", label.lower().strip())
    for prefix in _FUZZY_NOISE_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    cleaned = cleaned.rstrip("?").strip()
    tokens = [t for t in cleaned.split() if t not in _FUZZY_NOISE_WORDS and len(t) > 1]
    return " ".join(tokens) if tokens else cleaned


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _directional_overlap(query: frozenset[str], target: frozenset[str]) -> float:
    """Fraction of query tokens present in target — directional recall metric."""
    if not query:
        return 0.0
    return len(query & target) / len(query)


# ============================================================================
# MAIN CLASS
# ============================================================================
class Tier0Rules:
    """Deterministic form-aware SDTM mapping rules with domain inference fallback."""

    def resolve(self, form_code: str, field_label: str, form_name: str = "") -> ResolutionResult | None:
        """
        Return ResolutionResult if a rule matches, None otherwise.

        Resolution order:
          0. Learned mappings from reference aCRFs (conf 0.96)
          1. Compiled regex rules with EXACT form_code match (conf 0.98)
          2. Compiled regex rules with INFERRED DOMAIN match (conf 0.95)
          2.5. Universal domain-scoped rules from JSON config (conf 0.93)
          3. SDTM Standards lookup (conf 0.92)
          4. AZ Spec Lookup (conf 0.90)
        """
        if not form_code or not field_label:
            return None

        norm_label = normalize_label_for_lookup(field_label)
        form_upper = form_code.upper().strip()

        # ──────────────────────────────────────────────────────────────────
        # PASS 0: Learned mappings from reference aCRFs (highest priority)
        # ──────────────────────────────────────────────────────────────────
        result = self._try_learned_lookup(form_code, norm_label, field_label, form_name)
        if result:
            result = self._guard_domain(result)
            if result:
                return self._enrich_with_multi_mappings(result)

        # ──────────────────────────────────────────────────────────────────
        # PASS 1: Exact form-code scoped rules (highest confidence)
        # ──────────────────────────────────────────────────────────────────
        result = self._try_compiled_rules(
            form_upper, norm_label, field_label, form_code, _CONF_EXACT_FORM_RULE
        )
        if result:
            result = self._guard_domain(result)
            if result:
                return self._enrich_with_multi_mappings(result)

        # ──────────────────────────────────────────────────────────────────
        # PASS 2: Domain-inferred rule matching (slightly lower confidence)
        # ──────────────────────────────────────────────────────────────────
        inference = infer_domains_cached(form_code, form_name)
        if inference.domains and inference.confidence >= 0.70:
            for inferred_domain in inference.domains:
                inferred_upper = inferred_domain.upper()
                if inferred_upper != form_upper:
                    result = self._try_compiled_rules(
                        inferred_upper, norm_label, field_label,
                        form_code, _CONF_INFERRED_DOMAIN_RULE
                    )
                    if result:
                        result = self._guard_domain(result)
                        if result:
                            return self._enrich_with_multi_mappings(result)

        # ──────────────────────────────────────────────────────────────────
        # PASS 2.5: Universal domain-scoped rules from JSON config
        # ──────────────────────────────────────────────────────────────────
        if inference.domains and inference.confidence >= 0.70:
            for inferred_domain in inference.domains:
                matched_rule = match_domain_rules(inferred_domain, norm_label)
                if matched_rule:
                    if matched_rule.variable == "NOT_SUBMITTED":
                        return ResolutionResult(
                            form_code=form_code,
                            field_label=field_label,
                            sdtm_domain="",
                            sdtm_variable="NOT SUBMITTED",
                            codelist_code="",
                            is_supplemental=False,
                            confidence=_CONF_DOMAIN_JSON_RULE,
                            resolved=True,
                            tier=ResolutionTier.TIER0_EXACT,
                            is_not_submitted=True,
                            sdtm_label="",
                            core="",
                        )
                    if check_usage(form_code, matched_rule.domain, matched_rule.variable, field_label):
                        result = ResolutionResult(
                            form_code=form_code,
                            field_label=field_label,
                            sdtm_domain=matched_rule.domain,
                            sdtm_variable=matched_rule.variable,
                            codelist_code=matched_rule.codelist,
                            is_supplemental=matched_rule.is_supp,
                            confidence=_CONF_DOMAIN_JSON_RULE,
                            resolved=True,
                            tier=ResolutionTier.TIER0_EXACT,
                            is_not_submitted=False,
                            sdtm_label="",
                            core="",
                        )
                        result = self._guard_domain(result)
                        if result:
                            return self._enrich_with_multi_mappings(result)

        # ──────────────────────────────────────────────────────────────────
        # PASS 3: SDTM Standards lookup (conf 0.92)
        # ──────────────────────────────────────────────────────────────────
        result = self._try_standards_lookup(form_code, norm_label, field_label, form_name)
        if result:
            result = self._guard_domain(result)
            if result:
                return self._enrich_with_multi_mappings(result)

        # ──────────────────────────────────────────────────────────────────
        # PASS 4: AZ Spec Lookup — exact string match (conf 0.90)
        # ──────────────────────────────────────────────────────────────────
        result = self._try_az_spec_lookup(form_code, norm_label, field_label, form_name)
        if result:
            result = self._guard_domain(result)
            if result:
                return self._enrich_with_multi_mappings(result)

        # ──────────────────────────────────────────────────────────────────
        # PASS 4.5: AZ Spec Lookup — Jaccard fuzzy match (conf 0.72–0.86)
        # Catches labels that differ in phrasing but share key tokens.
        # ──────────────────────────────────────────────────────────────────
        result = self._try_az_spec_fuzzy(form_code, norm_label, field_label, form_name)
        if result:
            result = self._guard_domain(result)
            if result:
                return self._enrich_with_multi_mappings(result)

        # ──────────────────────────────────────────────────────────────────
        # PASS 5: Findings test-name fallback (conf 0.88)
        # On a Findings-domain form (LB/VS/EG), a field whose label IS a known
        # test name (e.g. "Glucose", "Urea", "Weight") maps to {D}ORRES — the
        # result variable — qualified by {D}TESTCD, matching the reference aCRF
        # convention (LBORRES where LBTESTCD = "GLUC"). Only reached when every
        # rule above misses, so it never overrides curated mappings.
        # ──────────────────────────────────────────────────────────────────
        result = self._try_findings_test(form_code, field_label, inference)
        if result:
            result = self._guard_domain(result)
            if result:
                return self._enrich_with_multi_mappings(result)

        return None

    def _try_findings_test(self, form_code, field_label, inference):
        """Resolve a bare Findings test-name label to {DOMAIN}ORRES + TESTCD where-clause."""
        if not inference or not inference.domains or inference.confidence < 0.70:
            return None
        from src.resolution.findings_qualifier import (
            _DOMAIN_TEST_MAP, _DOMAIN_TESTCD_VAR, _norm_label,
        )
        label_norm = _norm_label(field_label)
        if not label_norm:
            return None
        for dom in inference.domains:
            d = dom.upper()
            test_map = _DOMAIN_TEST_MAP.get(d)
            if not test_map:
                continue
            # Exact match only — substring matching on short codes (k, ca, pt…)
            # would be unsafe.
            code = test_map.get(label_norm)
            if code:
                testcd_var = _DOMAIN_TESTCD_VAR[d]
                return ResolutionResult(
                    form_code=form_code,
                    field_label=field_label,
                    sdtm_domain=d,
                    sdtm_variable=f"{d}ORRES",
                    codelist_code="",
                    is_supplemental=False,
                    confidence=0.88,
                    resolved=True,
                    tier=ResolutionTier.TIER0_EXACT,
                    where_clause=f'{testcd_var} = "{code}"',
                    sdtm_label="",
                    core="",
                )
        return None

    # ──────────────────────────────────────────────────────────────────────
    # ONCOLOGY DOMAIN GUARD
    # ──────────────────────────────────────────────────────────────────────
    def _guard_domain(self, result: ResolutionResult) -> ResolutionResult | None:
        """Block oncology-only domains in non-oncology studies."""
        if not result or result.is_not_submitted:
            return result
        if result.sdtm_domain in _ONCOLOGY_ONLY_DOMAINS and not _is_oncology_study():
            # Remap known oncology variables to PR equivalents
            var_map = {
                "TUDTC": "PRSTDTC",
                "TRDTC": "PRSTDTC",
                "TUORRES": "PRORRES",
                "TRORRES": "PRORRES",
                "TULOC": "PRLOC",
                "TRLOC": "PRLOC",
                "TRSTRESC": "PRORRES",
                "TUSTRESC": "PRORRES",
            }
            new_var = var_map.get(result.sdtm_variable)
            if new_var:
                result.sdtm_domain = "PR"
                result.sdtm_variable = new_var
            else:
                # Generic fallback: move to PR with same variable
                result.sdtm_domain = "PR"
        return result

    # ──────────────────────────────────────────────────────────────────────
    # PASS 0: LEARNED MAPPINGS LOOKUP
    # ──────────────────────────────────────────────────────────────────────
    def _try_learned_lookup(
        self, form_code: str, norm_label: str, field_label: str, form_name: str = ""
    ) -> ResolutionResult | None:
        """
        Look up field in learned mappings extracted from reference aCRFs.

        Search order:
          1. form_code|label (most specific)
          2. domain|label (domain-scoped)
          3. |label (fallback — any domain, requires occurrence_count >= 2)
        """
        if not _LEARNED_MAPPINGS:
            return None

        form_upper = form_code.upper().strip()

        # Determine domain hints
        domain_hints = []
        legacy_domain = _get_domain_for_form(form_code)
        if legacy_domain:
            domain_hints.append(legacy_domain)

        inference = infer_domains_cached(form_code, form_name)
        if inference.domains:
            for d in inference.domains:
                if d not in domain_hints:
                    domain_hints.append(d)

        # Build search keys in priority order
        keys_to_try = []
        keys_to_try.append(f"{form_upper}|{norm_label}")
        for domain in domain_hints:
            keys_to_try.append(f"{domain}|{norm_label}")
        keys_to_try.append(f"|{norm_label}")

        for key in keys_to_try:
            entry = _LEARNED_MAPPINGS.get(key)
            if not entry:
                continue

            # Require minimum occurrence for fallback (domain-less) keys
            if key.startswith("|") and entry.get("occurrence_count", 0) < 2:
                continue

            # Handle NOT SUBMITTED
            if entry.get("is_not_submitted", False):
                return ResolutionResult(
                    form_code=form_code,
                    field_label=field_label,
                    resolved=True,
                    tier=ResolutionTier.TIER0_EXACT,
                    confidence=_CONF_LEARNED,
                    sdtm_domain="",
                    sdtm_variable="NOT SUBMITTED",
                    sdtm_label="",
                    core="",
                    is_supplemental=False,
                    is_not_submitted=True,
                    codelist_code="",
                )

            domain = entry.get("domain", "")
            variable = entry.get("variable", "")

            if not domain or not variable:
                continue

            # Usage guard for non-form-specific matches
            if not key.startswith(form_upper):
                if not check_usage(form_code, domain, variable, field_label):
                    continue

            return ResolutionResult(
                form_code=form_code,
                field_label=field_label,
                resolved=True,
                tier=ResolutionTier.TIER0_EXACT,
                confidence=_CONF_LEARNED,
                sdtm_domain=domain,
                sdtm_variable=variable,
                sdtm_label="",
                core="",
                is_supplemental=entry.get("is_supplemental", False),
                is_not_submitted=False,
                codelist_code=entry.get("codelist_code", ""),
            )

        return None

    # ──────────────────────────────────────────────────────────────────────
    # COMPILED RULES CHECKER
    # ──────────────────────────────────────────────────────────────────────
    def _try_compiled_rules(
        self,
        form_to_match: str,
        norm_label: str,
        field_label: str,
        original_form_code: str,
        confidence: float,
    ) -> ResolutionResult | None:
        """
        Match form-scoped rules from config/rules/form_rules.json + gating_rules.json.

        form_to_match is either the exact form code (Pass 1) or an inferred
        domain string (Pass 2). Both are tested against rule.form_pattern.
        """
        rule = match_form_rules(form_to_match, norm_label)

        # Also try universal gating rules (form_pattern = ".*") — these fire
        # regardless of form code and map to NOT_SUBMITTED.
        if rule is None:
            rule = match_gating_rules(norm_label)

        if rule is None:
            return None

        if rule.variable == "NOT_SUBMITTED":
            return ResolutionResult(
                form_code=original_form_code,
                field_label=field_label,
                resolved=True,
                tier=ResolutionTier.TIER0_EXACT,
                confidence=confidence,
                sdtm_domain="",
                sdtm_variable="NOT SUBMITTED",
                sdtm_label="",
                core="",
                is_supplemental=False,
                is_not_submitted=True,
                codelist_code="",
            )

        # Usage guard only for inferred/fallback matches (Pass 2+)
        if confidence < _CONF_EXACT_FORM_RULE:
            if not check_usage(original_form_code, rule.domain, rule.variable, field_label):
                return None

        return ResolutionResult(
            form_code=original_form_code,
            field_label=field_label,
            resolved=True,
            tier=ResolutionTier.TIER0_EXACT,
            confidence=confidence,
            sdtm_domain=rule.domain,
            sdtm_variable=rule.variable,
            sdtm_label="",
            core="",
            is_supplemental=rule.is_supp,
            is_not_submitted=False,
            codelist_code=rule.codelist,
        )

    # ──────────────────────────────────────────────────────────────────────
    # AZ SPEC FUZZY LOOKUP (pass 4.5)
    # ──────────────────────────────────────────────────────────────────────
    def _try_az_spec_fuzzy(
        self,
        form_code: str,
        norm_label: str,
        field_label: str,
        form_name: str = "",
    ) -> ResolutionResult | None:
        """
        Fuzzy fallback using Jaccard token similarity against AZ spec labels.

        Only fires when the pre-tokenised index is available and the query
        has at least 2 tokens (single-word queries are too ambiguous).

        Combined score = Jaccard * 0.6 + directional_recall * 0.4
        Minimum threshold: combined >= 0.72 (calibrated against tier2 experience).
        """
        if not _AZ_SPEC_TOKENS:
            return None

        # Determine modules to search (same logic as exact lookup)
        form_upper   = form_code.upper().strip()
        base_form    = re.sub(r"\d+$", "", form_upper)
        us_prefix    = form_upper.split("_")[0] if "_" in form_upper else ""
        modules: list[str] = []

        for candidate in (form_upper, base_form, us_prefix):
            if candidate and candidate in _AZ_SPEC_TOKENS and candidate not in modules:
                modules.append(candidate)

        legacy_domain = _get_domain_for_form(form_code)
        if legacy_domain and legacy_domain not in modules and legacy_domain in _AZ_SPEC_TOKENS:
            modules.append(legacy_domain)

        inference = infer_domains_cached(form_code, form_name)
        for d in (inference.domains or []):
            for key in (d, f"SUPP{d}"):
                if key not in modules and key in _AZ_SPEC_TOKENS:
                    modules.append(key)

        if not modules:
            return None

        # Build query token variants
        query_tokens = frozenset(re.findall(r"[a-z0-9]+", norm_label.lower()))
        cleaned      = _fuzzy_clean(field_label)
        clean_tokens = frozenset(re.findall(r"[a-z0-9]+", cleaned)) if cleaned != norm_label else query_tokens

        best_score   = 0.0
        best_entries: list[dict] | None = None
        _MIN_SCORE   = 0.72

        for module in modules:
            mod_tokens = _AZ_SPEC_TOKENS.get(module, {})
            mod_data   = _AZ_SPEC_LOOKUP.get(module, {})

            for spec_label, spec_toks in mod_tokens.items():
                if len(spec_toks) < 2:
                    continue

                for qtoks in (query_tokens, clean_tokens):
                    if len(qtoks) < 2:
                        continue
                    j    = _jaccard(qtoks, spec_toks)
                    d_ov = _directional_overlap(qtoks, spec_toks)
                    score = j * 0.6 + d_ov * 0.4

                    if score > best_score and score >= _MIN_SCORE:
                        best_score   = score
                        best_entries = mod_data.get(spec_label)

        if not best_entries:
            return None

        # Confidence scales linearly from 0.72 (floor) → 0.86 (ceiling at score=1)
        raw_conf = 0.72 + (best_score - _MIN_SCORE) / (1.0 - _MIN_SCORE) * (0.86 - 0.72)
        confidence = min(round(raw_conf, 3), 0.86)

        # Pick best entry (primary mapping preferred, then non-supplemental)
        primary    = [e for e in best_entries if e.get("map_order", "") == "1"]
        candidates = primary if primary else best_entries
        non_supp   = [e for e in candidates if not e.get("is_supplemental", False)]
        chosen     = non_supp[0] if non_supp else candidates[0]

        sdtm_domain   = chosen.get("sdtm_domain", "")
        sdtm_variable = chosen.get("sdtm_variable", "")
        if not sdtm_domain or not sdtm_variable:
            return None

        is_supp = chosen.get("is_supplemental", False)
        if sdtm_domain.startswith("SUPP"):
            sdtm_domain = sdtm_domain[4:]
            is_supp = True

        if not check_usage(form_code, sdtm_domain, sdtm_variable, field_label):
            return None

        return ResolutionResult(
            form_code=form_code,
            field_label=field_label,
            resolved=True,
            tier=ResolutionTier.TIER0_EXACT,
            confidence=confidence,
            sdtm_domain=sdtm_domain,
            sdtm_variable=sdtm_variable,
            sdtm_label=chosen.get("sdtm_label", ""),
            core=chosen.get("core", ""),
            is_supplemental=is_supp,
            is_not_submitted=False,
            codelist_code=chosen.get("codelist_code", "") or "",
        )

    # ──────────────────────────────────────────────────────────────────────
    # MULTI-MAPPING ENRICHMENT
    # ──────────────────────────────────────────────────────────────────────
    def _enrich_with_multi_mappings(self, result: ResolutionResult) -> ResolutionResult:
        """Check if this field has additional SDTM mappings and attach them."""
        if not result.resolved or result.is_not_submitted:
            return result

        key = (result.sdtm_domain, result.sdtm_variable)
        if key not in _MULTI_MAP_TABLE:
            return result

        additional = []
        for mapping in _MULTI_MAP_TABLE[key]:
            form_condition = mapping.get("condition_form", "")
            if form_condition:
                if not re.search(form_condition, result.form_code, re.IGNORECASE):
                    continue

            label_condition = mapping.get("condition_label", "")
            if label_condition:
                if not re.search(label_condition, result.field_label, re.IGNORECASE):
                    continue

            additional.append({
                "sdtm_domain": mapping["domain"],
                "sdtm_variable": mapping["variable"],
                "sdtm_label": mapping.get("label", ""),
                "is_supplemental": mapping.get("is_supplemental", False),
            })

        if additional:
            result.additional_mappings = additional

        return result

    # ──────────────────────────────────────────────────────────────────────
    # SDTM STANDARDS LOOKUP
    # ──────────────────────────────────────────────────────────────────────
    def _try_standards_lookup(
        self, form_code: str, norm_label: str, field_label: str, form_name: str = ""
    ) -> ResolutionResult | None:
        """Try resolving via SDTM Standards index."""
        if not _STANDARDS_INDEX:
            return None

        domains_to_search: list[str] = []

        legacy_domain = _get_domain_for_form(form_code)
        if legacy_domain:
            domains_to_search.append(legacy_domain)

        inference = infer_domains_cached(form_code, form_name)
        if inference.domains:
            for d in inference.domains:
                if d not in domains_to_search:
                    domains_to_search.append(d)

        if not domains_to_search:
            return None

        for domain in domains_to_search:
            datasets = []
            if domain in _STANDARDS_INDEX:
                datasets.append(domain)
            supp = f"SUPP{domain}"
            if supp in _STANDARDS_INDEX:
                datasets.append(supp)

            for ds in datasets:
                result = self._search_standards_dataset(ds, norm_label, field_label, form_code)
                if result:
                    return result

        # Try stripped variants
        stripped_parens = _STRIP_PARENS.sub("", norm_label).strip()
        stripped_digits = _STRIP_TRAILING_DIGITS.sub("", norm_label).strip()

        for domain in domains_to_search:
            datasets = []
            if domain in _STANDARDS_INDEX:
                datasets.append(domain)
            supp = f"SUPP{domain}"
            if supp in _STANDARDS_INDEX:
                datasets.append(supp)

            if stripped_parens != norm_label:
                for ds in datasets:
                    result = self._search_standards_dataset(ds, stripped_parens, field_label, form_code)
                    if result:
                        return result

            if stripped_digits != norm_label and stripped_digits != stripped_parens:
                for ds in datasets:
                    result = self._search_standards_dataset(ds, stripped_digits, field_label, form_code)
                    if result:
                        return result

        return None

    def _search_standards_dataset(
        self, dataset: str, norm_label: str, field_label: str, form_code: str
    ) -> ResolutionResult | None:
        """Search a specific SDTM standards dataset for a label match."""
        ds_index = _STANDARDS_INDEX.get(dataset)
        if not ds_index or norm_label not in ds_index:
            return None

        entries = ds_index[norm_label]
        if not entries:
            return None

        if len(entries) == 1:
            entry = entries[0]
        else:
            entry = self._pick_best_entry(entries, field_label)

        return self._build_standards_result(entry, field_label, form_code)

    def _pick_best_entry(self, entries: list[dict], field_label: str) -> dict:
        """Pick the best matching entry when multiple exist for same label."""
        match = re.search(r"\s+(\d+)\s*(?:\(.*\))?\s*$", field_label.strip())
        if not match:
            for entry in entries:
                var = entry.get("variable", "")
                if not re.search(r"\d+$", var):
                    return entry
            return entries[0]

        crf_number = int(match.group(1))
        target_suffix = str(crf_number - 1) if crf_number > 1 else ""

        for entry in entries:
            var = entry.get("variable", "")
            var_match = re.search(r"(\d+)$", var)
            if target_suffix == "":
                if not var_match:
                    return entry
            else:
                if var_match and var_match.group(1) == target_suffix:
                    return entry
        return entries[0]

    def _build_standards_result(
        self, entry: dict, field_label: str, form_code: str
    ) -> ResolutionResult:
        """Build a ResolutionResult from a standards entry."""
        dataset = entry.get("dataset", "")
        is_supp = entry.get("is_supplemental", False)
        base_domain = entry.get("base_domain", "")

        if not base_domain:
            base_domain = dataset[4:] if dataset.startswith("SUPP") else dataset
        if dataset.startswith("SUPP"):
            is_supp = True

        return ResolutionResult(
            form_code=form_code,
            field_label=field_label,
            resolved=True,
            tier=ResolutionTier.TIER0_EXACT,
            confidence=_CONF_STANDARDS,
            sdtm_domain=base_domain,
            sdtm_variable=entry.get("variable", ""),
            sdtm_label=entry.get("label", ""),
            core=entry.get("core", ""),
            is_supplemental=is_supp,
            is_not_submitted=False,
            codelist_code=entry.get("codelist_code", ""),
        )

    # ──────────────────────────────────────────────────────────────────────
    # AZ SPEC LOOKUP
    # ──────────────────────────────────────────────────────────────────────
    def _try_az_spec_lookup(
        self, form_code: str, norm_label: str, field_label: str, form_name: str = ""
    ) -> ResolutionResult | None:
        """Try resolving via AZ Spec Lookup."""
        if not _AZ_SPEC_LOOKUP:
            return None

        form_upper = form_code.upper().strip()
        base_form = re.sub(r"\d+$", "", form_upper)
        underscore_prefix = form_upper.split("_")[0] if "_" in form_upper else ""

        modules_to_try: list[str] = []

        if form_upper in _AZ_SPEC_LOOKUP:
            modules_to_try.append(form_upper)
        if base_form != form_upper and base_form in _AZ_SPEC_LOOKUP:
            modules_to_try.append(base_form)
        if underscore_prefix and underscore_prefix != form_upper and underscore_prefix != base_form:
            if underscore_prefix in _AZ_SPEC_LOOKUP:
                modules_to_try.append(underscore_prefix)

        legacy_domain = _get_domain_for_form(form_code)
        if legacy_domain and legacy_domain not in modules_to_try and legacy_domain in _AZ_SPEC_LOOKUP:
            modules_to_try.append(legacy_domain)

        inference = infer_domains_cached(form_code, form_name)
        if inference.domains:
            for d in inference.domains:
                if d not in modules_to_try and d in _AZ_SPEC_LOOKUP:
                    modules_to_try.append(d)
                supp_d = f"SUPP{d}"
                if supp_d not in modules_to_try and supp_d in _AZ_SPEC_LOOKUP:
                    modules_to_try.append(supp_d)

        if not modules_to_try:
            return None

        labels_to_try = [norm_label]
        stripped_parens = _STRIP_PARENS.sub("", norm_label).strip()
        if stripped_parens != norm_label:
            labels_to_try.append(stripped_parens)
        stripped_digits = _STRIP_TRAILING_DIGITS.sub("", norm_label).strip()
        if stripped_digits != norm_label and stripped_digits not in labels_to_try:
            labels_to_try.append(stripped_digits)

        for module in modules_to_try:
            module_data = _AZ_SPEC_LOOKUP.get(module)
            if not module_data:
                continue
            for label in labels_to_try:
                if label not in module_data:
                    continue
                entries = module_data[label]
                if not entries:
                    continue
                entry = entries[0]
                sdtm_domain = entry.get("sdtm_domain", "")
                sdtm_variable = entry.get("sdtm_variable", "")
                if not sdtm_domain or not sdtm_variable:
                    continue

                is_supp = entry.get("is_supplemental", False)
                display_domain = sdtm_domain
                if sdtm_domain.startswith("SUPP"):
                    display_domain = sdtm_domain[4:]
                    is_supp = True

                return ResolutionResult(
                    form_code=form_code,
                    field_label=field_label,
                    resolved=True,
                    tier=ResolutionTier.TIER0_EXACT,
                    confidence=_CONF_AZ_SPEC,
                    sdtm_domain=display_domain,
                    sdtm_variable=sdtm_variable,
                    sdtm_label=entry.get("sdtm_label", ""),
                    core=entry.get("core", ""),
                    is_supplemental=is_supp,
                    is_not_submitted=False,
                    codelist_code=entry.get("codelist_code", "") or "",
                )

        return None