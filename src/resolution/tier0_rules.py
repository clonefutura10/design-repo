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
from src.resolution.rule_loader import match_domain_rules

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
# RULES TABLE
# (form_pattern, label_pattern, domain, variable, codelist, is_supp)
# First match wins - put SPECIFIC patterns BEFORE general ones.
# ============================================================================
_RULES: list[tuple[str, str, str, str, str, bool]] = [
    # =========================================================================
    # VISIT / SV
    # =========================================================================
    (r"^VISIT\d?$", r"^visit\s*date$", "SV", "SVSTDTC", "", False),
    (r"^VISIT\d?$", r"^visit\s*not\s*done$", "SV", "SVENDTC", "", False),

    # =========================================================================
    # CONSENT / DS
    # =========================================================================
    (r"^CONSENT\d?$", r"date\s*subject\s*signed\s*main\s*informed\s*consent", "DS", "DSSTDTC", "", False),
    (r"^CONSENT\d?$", r"date\s*subject\s*signed\s*main\s*informed\s*assent", "DS", "DSSTDTC", "", False),
    (r"^CONSENT\d?$", r"date\s*of\s*optional\s*consent", "DS", "DSSTDTC", "", False),
    (r"^CONSENT\d?$", r"date\s*of\s*domiciliary", "DS", "DSSTDTC", "", False),
    (r"^CONSENT\d?$", r"optional\s*consent.*category", "DS", "DSTERM", "", False),
    (r"^CONSENT\d?$", r"assessment\s*applicable", "DS", "NOT_SUBMITTED", "", False),
    (r"^CONSENT\d?$", r"is\s*the\s*subject\s*participating", "DS", "DSTERM", "", False),

    # =========================================================================
    # DM
    # =========================================================================
    (r"^DM$", r"^birth\s*date$", "DM", "BRTHDTC", "", False),
    (r"^DM$", r"^date\s*of\s*birth$", "DM", "BRTHDTC", "", False),
    (r"^DM$", r"^age$", "DM", "AGE", "", False),
    (r"^DM$", r"^age\s*as\s*collected$", "DM", "AGE", "", False),
    (r"^DM$", r"^age\s*unit$", "DM", "AGEU", "", False),
    (r"^DM$", r"^sex$", "DM", "SEX", "C66731", False),
    (r"^DM$", r"^ethnicity$", "DM", "ETHNIC", "C66790", False),
    (r"^DM$", r"^race$", "DM", "RACE", "C74457", False),
    (r"^DM$", r"^specify\s*other$", "DM", "RACEOTH", "", True),
    (r"^DM$", r"^if\s*other\s*race", "DM", "RACEOTH", "", True),
    (r"^DM$", r"field\s*created\s*for\s*rsg", "DM", "NOT_SUBMITTED", "", False),
    (r"^DM$", r"date\s*of\s*birth\s*will\s*be\s*integrated", "DM", "NOT_SUBMITTED", "", False),
    (r"^DM$", r"^calculating\s*age$", "DM", "NOT_SUBMITTED", "", False),

    # =========================================================================
    # VS / VS1
    # =========================================================================
    (r"^VS\d?$", r"were\s*vital\s*signs\s*collected", "VS", "VSSTAT", "", False),
    (r"^VS\d?$", r"was\s*the\s*vital\s*signs?\s*examination\s*performed", "VS", "VSSTAT", "", False),
    (r"^VS\d?$", r"vital\s*sign[s]?\s*(collection\s*)?date", "VS", "VSDTC", "", False),
    (r"^VS\d?$", r"^examination\s*date$", "VS", "VSDTC", "", False),
    (r"^VS\d?$", r"vital\s*sign[s]?\s*test\s*name", "VS", "VSTEST", "C67153", False),
    (r"^VS\d?$", r"^result$", "VS", "VSORRES", "", False),
    (r"^VS\d?$", r"^unit$", "VS", "VSORRESU", "C66770", False),
    (r"^VS\d?$", r"was\s*the\s*result\s*clinically\s*significant", "VS", "VSCLSIG", "C66742", False),
    (r"^VS\d?$", r"field\s*created\s*for\s*rsg", "VS", "NOT_SUBMITTED", "", False),
    (r"^VS\d?$", r"^heart\s*rate$", "VS", "VSORRES", "", False),
    (r"^VS\d?$", r"^respiratory\s*rate$", "VS", "VSORRES", "", False),
    (r"^VS\d?$", r"^systolic\s*blood\s*pressure$", "VS", "VSORRES", "", False),
    (r"^VS\d?$", r"^diastolic\s*blood\s*pressure$", "VS", "VSORRES", "", False),
    (r"^VS\d?$", r"^temperature$", "VS", "VSORRES", "", False),
    (r"^VS\d?$", r"^height$", "VS", "VSORRES", "", False),
    (r"^VS\d?$", r"^weight$", "VS", "VSORRES", "", False),
    (r"^VS\d?$", r"^bmi\b", "VS", "VSORRES", "", False),

    # =========================================================================
    # EG — "reason" MUST come BEFORE "overall" (first match wins)
    # =========================================================================
    (r"^EG$", r"was\s*the\s*ecg\s*performed", "EG", "EGSTAT", "C66789", False),
    (r"^EG$", r"date\s*of\s*ecg", "EG", "EGDTC", "", False),
    (r"^EG$", r"reason.{0,5}abnormal", "EG", "EGREAS", "", True),
    (r"^EG$", r"overall\s*ecg\s*evaluation", "EG", "EGORRES", "", False),
    (r"^EG$", r"was\s*the\s*ecg\s*clinically\s*significant", "EG", "EGCLSIG", "", True),

    # =========================================================================
    # PE
    # =========================================================================
    (r"^PE\d?$", r"was\s*the\s*physical\s*examination\s*performed", "PR", "PROCCUR", "C66742", False),
    (r"^PE\d?$", r"physical\s*examination\s*date", "PR", "PRSTDTC", "", False),
    (r"^PE\d?$", r"^examination\s*date$", "PR", "PRSTDTC", "", False),

    # =========================================================================
    # MH — Gating question → NOT SUBMITTED
    # =========================================================================
    (r"^MH$", r"does\s*the\s*subject\s*have\s*any\s*past", "MH", "NOT_SUBMITTED", "", False),
    (r"^MH$", r"has\s*the\s*subject\s*had\s*any\s*medical\s*conditions", "MH", "NOT_SUBMITTED", "", False),
    (r"^MH$", r"has\s*subject\s*any\s*relevant\s*medical", "MH", "NOT_SUBMITTED", "", False),
    (r"^MH$", r"has\s*the\s*subject\s*experienced\s*any\s*past", "MH", "NOT_SUBMITTED", "", False),
    (r"^MH$", r"medical\s*history\s*verbatim\s*term", "MH", "MHTERM", "", False),
    (r"^MH$", r"^what\s*is\s*the\s*medical\s*condition", "MH", "MHTERM", "", False),
    (r"^MH$", r"^medical\s*history\s*term$", "MH", "MHTERM", "", False),
    (r"^MH$", r"^condition$", "MH", "MHTERM", "", False),
    (r"^MH$", r"start\s*date\s*of\s*condition", "MH", "MHSTDTC", "", False),
    (r"^MH$", r"^start\s*date$", "MH", "MHSTDTC", "", False),
    (r"^MH$", r"end\s*date\s*of\s*condition", "MH", "MHENDTC", "", False),
    (r"^MH$", r"^end\s*date$", "MH", "MHENDTC", "", False),
    (r"^MH$", r"condition\s*ongoing", "MH", "MHENRF", "", False),
    (r"^MH$", r"^ongoing", "MH", "MHENRF", "", False),
    (r"^MH$", r"condition.*past\s*or\s*current", "MH", "MHENRF", "", False),
    (r"^MH$", r"taking\s*current\s*medication", "MH", "MHCURM", "", True),
    (r"^MH$", r"any\s*current\s*medication", "MH", "MHCURM", "", True),
    (r"^MH$", r"medical\s*condition\s*under\s*control", "MH", "MHCONTRL", "", True),
    # MedDRA coding fields
    (r"^MH$", r"meddra\s*lowest\s*level\s*term\s*code", "MH", "MHLLTCD", "", False),
    (r"^MH$", r"meddra\s*lowest\s*level\s*term\s*name", "MH", "MHLLT", "", False),
    (r"^MH$", r"meddra\s*preferred\s*term\s*code", "MH", "MHPTCD", "", False),
    (r"^MH$", r"meddra\s*preferred\s*term\s*name", "MH", "MHDECOD", "", False),
    (r"^MH$", r"meddra\s*high\s*level\s*term\s*code", "MH", "MHHLTCD", "", False),
    (r"^MH$", r"meddra\s*high\s*level\s*term\s*name", "MH", "MHHLT", "", False),
    (r"^MH$", r"meddra\s*high\s*level\s*group\s*term\s*code", "MH", "MHHLGTCD", "", False),
    (r"^MH$", r"meddra\s*high\s*level\s*group\s*term\s*name", "MH", "MHHLGT", "", False),
    (r"^MH$", r"meddra\s*system\s*organ\s*class\s*code", "MH", "MHBDSYCD", "", False),
    (r"^MH$", r"meddra\s*system\s*organ\s*class\s*name", "MH", "MHBODSYS", "", False),
    (r"^MH$", r"meddra\s*system\s*organ\s*class\s*abbreviation", "MH", "MHBODSYS", "", True),
    (r"^MH$", r"meddra\s*version", "MH", "MEDDRAV", "", True),

    # =========================================================================
    # CM / CM1 - NUMBERED SUFFIX FIELDS (must come BEFORE generic patterns)
    # =========================================================================
    (r"^CM\d?$", r"^medication\s*dictionary\s*text\s*8$", "CM", "CMDTXT7", "", True),
    (r"^CM\d?$", r"^medication\s*dictionary\s*text\s*7$", "CM", "CMDTXT6", "", True),
    (r"^CM\d?$", r"^medication\s*dictionary\s*text\s*6$", "CM", "CMDTXT5", "", True),
    (r"^CM\d?$", r"^medication\s*dictionary\s*text\s*5$", "CM", "CMDTXT4", "", True),
    (r"^CM\d?$", r"^medication\s*dictionary\s*text\s*4$", "CM", "CMDTXT3", "", True),
    (r"^CM\d?$", r"^medication\s*dictionary\s*text\s*3$", "CM", "CMDTXT2", "", True),
    (r"^CM\d?$", r"^medication\s*dictionary\s*text\s*2$", "CM", "CMDTXT1", "", True),
    (r"^CM\d?$", r"^medication\s*dictionary\s*text$", "CM", "CMDTXT", "", True),
    (r"^CM\d?$", r"^preferred\s*name\s*8", "CM", "CMPREF8", "", True),
    (r"^CM\d?$", r"^preferred\s*name\s*7", "CM", "CMPREF7", "", True),
    (r"^CM\d?$", r"^preferred\s*name\s*6", "CM", "CMPREF6", "", True),
    (r"^CM\d?$", r"^preferred\s*name\s*5", "CM", "CMPREF5", "", True),
    (r"^CM\d?$", r"^preferred\s*name\s*4", "CM", "CMPREF4", "", True),
    (r"^CM\d?$", r"^preferred\s*name\s*3", "CM", "CMPREF3", "", True),
    (r"^CM\d?$", r"^preferred\s*name\s*2", "CM", "CMPREF2", "", True),
    (r"^CM\d?$", r"^preferred\s*name\s*\(", "CM", "CMDECOD", "", False),
    (r"^CM\d?$", r"^preferred\s*name$", "CM", "CMDECOD", "", False),
    (r"^CM\d?$", r"^active\s*ingredient\s*2$", "CM", "CMINGRD2", "", True),
    (r"^CM\d?$", r"^active\s*ingredient\s*1$", "CM", "CMINGRD1", "", True),
    (r"^CM\d?$", r"^active\s*ingredient$", "CM", "CMINGRD1", "", True),
    (r"^CM\d?$", r"^pref\.?\s*grouping\s*term", "CM", "CMGROUP", "", True),

    # =========================================================================
    # CM / CM1 - Main fields
    # =========================================================================
    (r"^CM\d?$", r"was\s*the\s*subject\s*ever\s*treated", "CM", "NOT_SUBMITTED", "", False),
    (r"^CM\d?$", r"has\s*the\s*subject\s*had\s*any\s*cancer\s*therapy", "CM", "NOT_SUBMITTED", "", False),
    (r"^CM\d?$", r"^any\s*medications$", "CM", "CMTRT", "", False),
    (r"^CM\d?$", r"medication\s*number", "CM", "CMSPID", "", False),
    (r"^CM\d?$", r"medication\s*or\s*therapy", "CM", "CMTRT", "", False),
    (r"^CM\d?$", r"medication\s*verbatim\s*name", "CM", "CMTRT", "", False),
    (r"^CM\d?$", r"^medication\s*name$", "CM", "CMTRT", "", False),
    (r"^CM\d?$", r"^cancer\s*therapy\s*agent$", "CM", "CMTRT", "", False),
    (r"^CM\d?$", r"combination\s*drug", "CM", "CMTRT", "", False),
    (r"^CM\d?$", r"medication\s*route", "CM", "CMROUTE", "", False),
    (r"^CM\d?$", r"agent\s*route\s*of\s*administration", "CM", "CMROUTE", "", False),
    (r"^CM\d?$", r"inhalator\s*type", "CM", "CMROUTE", "", False),
    (r"^CM\d?$", r"depo.*injection.*intramuscular", "CM", "CMROUTE", "", False),
    (r"^CM\d?$", r"treatment\s*start\s*date", "CM", "CMSTDTC", "", False),
    (r"^CM\d?$", r"cancer\s*therapy\s*agent\s*start\s*date", "CM", "CMSTDTC", "", False),
    (r"^CM\d?$", r"^start\s*date$", "CM", "CMSTDTC", "", False),
    (r"^CM\d?$", r"treatment\s*prior\s*to\s*study", "CM", "CMSTRF", "AZC00378", False),
    (r"^CM\d?$", r"treatment\s*stop\s*date", "CM", "CMENDTC", "", False),
    (r"^CM\d?$", r"cancer\s*therapy\s*agent\s*stop\s*date", "CM", "CMENDTC", "", False),
    (r"^CM\d?$", r"^end\s*date$", "CM", "CMENDTC", "", False),
    (r"^CM\d?$", r"treatment\s*continues", "CM", "CMENRF", "AZC00378", False),
    (r"^CM\d?$", r"^reason\s*for\s*treatment\s*stop$", "CM", "CMREAS", "", True),
    (r"^CM\d?$", r"other\s*reason\s*for\s*treatment\s*stop", "CM", "CMREASOT", "", True),
    (r"^CM\d?$", r"reason\s*for\s*therapy", "CM", "CMINDC", "", False),
    (r"^CM\d?$", r"therapy\s*reason.*other", "CM", "CMREASOT", "", True),
    (r"^CM\d?$", r"^therapy\s*reason$", "CM", "CMINDC", "", False),
    (r"^CM\d?$", r"^indication$", "CM", "CMINDC", "", False),
    (r"^CM\d?$", r"^therapy\s*class$", "CM", "CMCLAS", "", False),
    (r"^CM\d?$", r"dose\s*per\s*administration", "CM", "CMDOSE", "", False),
    (r"^CM\d?$", r"^dose$", "CM", "CMDOSE", "", False),
    (r"^CM\d?$", r"total\s*daily\s*dose", "CM", "CMDOSTOT", "", False),
    (r"^CM\d?$", r"dose\s*unit.*other", "CM", "CMDOSUO", "", True),
    (r"^CM\d?$", r"^dose\s*unit$", "CM", "CMDOSU", "", False),
    (r"^CM\d?$", r"dosing\s*frequency.*other", "CM", "CMDOSFRQO", "", True),
    (r"^CM\d?$", r"medication\s*dosing\s*frequency$", "CM", "CMDOSFRQ", "", False),
    (r"^CM\d?$", r"action\s*taken.*end\s*of\s*medication", "CM", "CMACTTK", "", False),
    (r"^CM\d?$", r"^medication\s*code$", "CM", "CMCD", "", True),
    (r"^CM\d?$", r"^atc\s*code$", "CM", "CMCLASCD", "", False),
    (r"^CM\d?$", r"^atc\s*dictionary\s*text$", "CM", "CMCLAS", "", False),
    (r"^CM\d?$", r"^drug\s*dictionary\s*version$", "CM", "WHODRGV", "", True),
    (r"^CM\d?$", r"^treatment\s*status$", "CM", "CMTRTSTS", "", True),
    (r"^CM\d?$", r"^best\s*overall\s*response$", "CM", "CMBORSLT", "", True),

    # =========================================================================
    # LB3 (Urinalysis)
    # =========================================================================
    (r"^LB3$", r"was\s*the\s*sample\s*collected", "LB", "LBSTAT", "", False),
    (r"^LB3$", r"collection\s*date", "LB", "LBDTC", "", False),
    (r"^LB3$", r"what\s*was\s*the\s*lab\s*panel", "LB", "LBCAT", "", False),
    (r"^LB3$", r"accession\s*number", "LB", "LBREFID", "", False),
    (r"^LB3$", r"what\s*is\s*the\s*test\s*name", "LB", "LBTEST", "", False),
    (r"^LB3$", r"laboratory\s*value\s*dipstick", "LB", "LBORRES", "", False),

    # =========================================================================
    # LB (general)
    # =========================================================================
    (r"^LB\d?$", r"^ae\s*number$", "LB", "LBREFID", "", False),
    (r"^LB\d?$", r"was\s*the\s*sample\s*collected", "LB", "LBSTAT", "", False),
    (r"^LB\d?$", r"was\s*local\s*lab\s*performed", "LB", "LBSTAT", "", False),
    (r"^LB\d?$", r"was\s*local\s*hematology\s*performed", "LB", "LBSTAT", "", False),
    (r"^LB\d?$", r"was\s*local\s*bio.*chemistry\s*performed", "LB", "LBSTAT", "", False),
    (r"^LB\d?$", r"if\s*no.*please\s*specify", "LB", "LBREASND", "", True),
    (r"^LB\d?$", r"(collection|specimen\s*collection)\s*date", "LB", "LBDTC", "", False),
    (r"^LB\d?$", r"collection\s*time", "LB", "LBTM", "", False),
    (r"^LB\d?$", r"^panel\s*name$", "LB", "LBCAT", "", False),
    (r"^LB\d?$", r"alanine\s*aminotransferase", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"aspartate\s*aminotransferase", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"alkaline\s*phosphatase", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"gamma.?glutamyl\s*transferase", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"bilirubin.*total", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"^albumin$", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"^creatinine$", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"blood\s*urea\s*nitrogen", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"^potassium", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"^sodium", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"^hb$", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"^haematocrit$", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"^rbc$", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"leukocyte\s*count", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"platelet\s*count", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"eosinophils", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"neutrophils", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"basophils", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"lymphocytes", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"leucocytes.*wbc", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"serum\s*tryptase", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"protocol\s*timepoint", "LB", "LBTPT", "", False),
    (r"^LB\d?$", r"activated\s*partial\s*thromboplastin", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"international\s*normalized\s*ratio", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"cholesterol.*total", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"^appearance\s*and\s*colo", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"microscopic\s*(?:red|white)\s*blood", "LB", "LBORRES", "", False),
    (r"^LB\d?$", r"occult\s*blood", "LB", "LBORRES", "", False),

    # =========================================================================
    # PREG
    # =========================================================================
    (r"^PREG$", r"was\s*pregnancy\s*test\s*performed", "LB", "LBSTAT", "", False),
    (r"^PREG$", r"was\s*the\s*pregnancy\s*test\s*performed", "LB", "LBSTAT", "", False),
    (r"^PREG$", r"sampling\s*date", "LB", "LBDTC", "", False),
    (r"^PREG$", r"test\s*result", "LB", "LBORRES", "", False),
    (r"^PREG$", r"pregnancy\s*result", "LB", "LBORRES", "", False),
    (r"^PREG$", r"pregnancy\s*test", "LB", "LBTEST", "", False),

    # =========================================================================
    # RP (Reproductive/Pregnancy)
    # FIX: "Was the Child Bearing Potential?" is a gating question → NOT SUBMITTED
    # =========================================================================
    (r"^RP$", r"was\s*the\s*child\s*bearing\s*potential", "RP", "NOT_SUBMITTED", "", False),
    (r"^RP$", r"was\s*the\s*pregnancy\s*test\s*performed", "RP", "RPOCCUR", "", False),
    (r"^RP$", r"collection\s*date", "RP", "RPDTC", "", False),
    (r"^RP$", r"collection\s*time", "RP", "RPTM", "", False),
    (r"^RP$", r"pregnancy\s*result", "RP", "RPORRES", "", False),

    # =========================================================================
    # SU_NIC
    # =========================================================================
    (r"^SU_NIC$", r"category\s*of\s*substance", "SU", "SUCAT", "AZC00536", False),
    (r"^SU_NIC$", r"^substance\s*use\s*category$", "SU", "SUCAT", "AZC00536", False),
    (r"^SU_NIC$", r"smoking\s*status", "SU", "SUSTATUS", "", True),
    (r"^SU_NIC$", r"^substance\s*user$", "SU", "SUSTATUS", "", True),
    (r"^SU_NIC$", r"has\s*the\s*subject\s*ever\s*used", "SU", "SUSTATUS", "", True),
    (r"^SU_NIC$", r"number\s*of\s*pack\s*years", "SU", "SUPKYR", "", True),
    (r"^SU_NIC$", r"^start\s*date$", "SU", "SUSTDTC", "", False),
    (r"^SU_NIC$", r"what\s*was\s*the\s*start\s*date", "SU", "SUSTDTC", "", False),
    (r"^SU_NIC$", r"^end\s*date$", "SU", "SUENDTC", "", False),
    (r"^SU_NIC$", r"what\s*was\s*the\s*end\s*date", "SU", "SUENDTC", "", False),
    (r"^SU_NIC$", r"status\s*no\s*change", "SU", "NOT_SUBMITTED", "", False),

    # =========================================================================
    # SU (general substance use)
    # =========================================================================
    (r"^SU$", r"smoking\s*status", "SU", "SUSTATUS", "", True),
    (r"^SU$", r"^start\s*date$", "SU", "SUSTDTC", "", False),
    (r"^SU$", r"^stop\s*date", "SU", "SUENDTC", "", False),
    (r"^SU$", r"number\s*of\s*pack\s*years", "SU", "SUPKYR", "", True),
    (r"^SU$", r"status\s*no\s*change", "SU", "NOT_SUBMITTED", "", False),

    # =========================================================================
    # SU_ALC (Alcohol)
    # =========================================================================
    (r"^SU_?ALC$", r"substance\s*use\s*category", "SU", "SUCAT", "", False),
    (r"^SU_?ALC$", r"alcohol\s*use\s*occur", "SU", "SUSTATUS", "", True),
    (r"^SU_?ALC$", r"alcohol\s*consumption", "SU", "SUDOSE", "", False),
    (r"^SU_?ALC$", r"substance\s*use\s*frequency", "SU", "SUFREQ", "", False),

    # =========================================================================
    # ALLERH
    # =========================================================================
    (r"^ALLERH$", r"any\s*allergy", "MH", "MHOCCUR", "", False),
    (r"^ALLERH$", r"allergen\s*class", "MH", "MHSCAT", "", False),
    (r"^ALLERH$", r"allergy\s*history", "MH", "MHOCCUR", "", False),
    (r"^ALLERH$", r"allergen\s*agent", "MH", "MHTERM", "", False),
    (r"^ALLERH$", r"specify\s*other\s*allergen", "MH", "ALLEROTH", "", True),

    # =========================================================================
    # HISS
    # =========================================================================
    (r"^HISS$", r"has\s*subject\s*undergone", "PR", "PROCCUR", "C66742", False),
    (r"^HISS$", r"has\s*the\s*subject\s*had\s*any\s*relevant\s*surgery", "PR", "PROCCUR", "C66742", False),
    (r"^HISS$", r"surgical\s*history\s*verbatim", "PR", "PRTERM", "", False),
    (r"^HISS$", r"^surgical\s*procedure(\s*term)?$", "PR", "PRTERM", "", False),
    (r"^HISS$", r"date\s*of\s*surgery", "PR", "PRSTDTC", "", False),
    (r"^HISS$", r"^start\s*date$", "PR", "PRSTDTC", "", False),
    (r"^HISS$", r"taking\s*current\s*medication", "PR", "PRCURM", "", True),
    (r"^HISS$", r"^current\s*medication$", "PR", "PRCURM", "", True),
    (r"^HISS$", r"meddra\s*lowest\s*level\s*term\s*code", "PR", "PRLLTCD", "", True),
    (r"^HISS$", r"meddra\s*lowest\s*level\s*term\s*name", "PR", "PRLLT", "", True),
    (r"^HISS$", r"meddra\s*preferred\s*term\s*code", "PR", "PRPTCD", "", True),
    (r"^HISS$", r"meddra\s*preferred\s*term\s*name", "PR", "PRDECOD", "", False),
    (r"^HISS$", r"meddra\s*high\s*level\s*term\s*code", "PR", "PRHLTCD", "", True),
    (r"^HISS$", r"meddra\s*high\s*level\s*term\s*name", "PR", "PRHLT", "", True),
    (r"^HISS$", r"meddra\s*high\s*level\s*group\s*term\s*code", "PR", "PRHLGTCD", "", True),
    (r"^HISS$", r"meddra\s*high\s*level\s*group\s*term\s*name", "PR", "PRHLGT", "", True),
    (r"^HISS$", r"meddra\s*system\s*organ\s*class\s*code", "PR", "PRBDSYCD", "", True),
    (r"^HISS$", r"meddra\s*system\s*organ\s*class\s*name", "PR", "PRBODSYS", "", True),
    (r"^HISS$", r"meddra\s*system\s*organ\s*class\s*abbreviation", "PR", "PRBDABB", "", True),
    (r"^HISS$", r"meddra\s*version", "PR", "MEDDRAV", "", True),

    # =========================================================================
    # ASMPERF
    # =========================================================================
    (r"^ASMPERF\d?$", r"assessment.*type", "PR", "PRCAT", "", False),
    (r"^ASMPERF\d?$", r"reason\s*assessment.*not\s*performed", "PR", "PRREASND", "", True),
    (r"^ASMPERF\d?$", r"assessment.*performed", "PR", "PROCCUR", "C66742", False),
    (r"^ASMPERF\d?$", r"assessment\s*date", "PR", "PRSTDTC", "", False),
    (r"^ASMPERF\d?$", r"protocol\s*schedule", "PR", "PRSCAT", "", False),
    (r"^ASMPERF\d?$", r"select.*yes.*populate", "PR", "PRSPFY", "", True),
    (r"^ASMPERF\d?$", r"accession\s*id", "PR", "PRREFID", "", False),
    (r"^ASMPERF\d?$", r"^comment$", "PR", "PRCOMM", "", True),

    # =========================================================================
    # RESHISTE
    # =========================================================================
    (r"^RESHISTE$", r"asthma\s*first\s*diagnosed\s*date", "MH", "MHSTDTC", "", False),
    (r"^RESHISTE$", r"first\s*appearance\s*of\s*asthma", "FA", "FADTC", "C101833", False),
    (r"^RESHISTE$", r"total\s*number\s*of\s*exacerbations", "FA", "FAORRES", "", False),
    (r"^RESHISTE$", r"number\s*of\s*exacerbations\s*resulted", "FA", "FAORRES", "", False),
    (r"^RESHISTE$", r"number\s*of\s*exacerbations\s*documented", "FA", "FAORRES", "", False),
    (r"^RESHISTE$", r"number\s*of\s*exacerbations", "FA", "FAORRES", "", False),
    (r"^RESHISTE$", r"diagnosis\s*of\s*rhinitis", "MH", "MHTERM", "", False),
    (r"^RESHISTE$", r"diagnosis\s*of", "MH", "MHTERM", "", False),
    (r"^RESHISTE$", r"near\s*fatal\s*asthma", "FA", "FASPFY", "", True),
    (r"^RESHISTE$", r"rhinitis\s*seasonal", "MH", "MHSCAT", "", False),
    (r"^RESHISTE$", r"nasal\s*polyps", "MH", "MHTERM", "", False),
    (r"^RESHISTE$", r"corticosteroids", "MH", "MHTERM", "", False),

    # =========================================================================
    # HEVENT
    # =========================================================================
    (r"^HEVENT\d?$", r"any\s*asthma.*related\s*event\s*since", "FAHO", "FATESTCD", "C101832", False),
    (r"^HEVENT\d?$", r"any\s*asthma.*related\s*event", "FAHO", "FATESTCD", "C101832", False),
    (r"^HEVENT\d?$", r"number\s*of\s*ambulance", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*days", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*emergency", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*hospital", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*visits", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*other", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*home", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*telephone", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*spirometry", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*advanced", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*plain", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"number\s*of\s*computer", "FAHO", "FAORRES", "", False),
    (r"^HEVENT\d?$", r"emergency\s*room\s*visit\s*date", "HO", "HOSTDTC", "", False),
    (r"^HEVENT\d?$", r"oxygen\s*initiated\s*date", "CM", "CMSTDTC", "", False),

    # =========================================================================
    # CHCSS
    # =========================================================================
    (r"^CHCSS$", r"any\s*non.*asthma.*related", "HO", "HOSCAT", "AZC00454", False),
    (r"^CHCSS$", r"date\s*of\s*emergency", "HO", "HOSTDTC", "", False),
    (r"^CHCSS$", r"ae\s*number", "HO", "HOSENUM", "", True),

    # =========================================================================
    # PULMTE
    # =========================================================================
    (r"^PULMTE$", r"documented\s*historical\s*reversibility.*available", "FA", "FASTAT", "", False),
    (r"^PULMTE$", r"historical\s*reversibility\s*date", "FA", "FADTC", "", False),
    (r"^PULMTE$", r"fev1\s*reversibility\s*\(ml\)", "FA", "FAORRES", "", False),
    (r"^PULMTE$", r"fev1\s*reversibility\s*\(%\)", "FA", "FAORRES", "", False),
    (r"^PULMTE$", r"fev1\s*reversibility", "FA", "FAORRES", "", False),
    (r"^PULMTE$", r"protocol\s*schedule", "FA", "FATPT", "AZC02551", False),
    (r"^PULMTE$", r"fev1.*historical\s*value", "FA", "FAORRES", "", False),

    # =========================================================================
    # SPC: BIOMARKER BLOOD → BE domain
    # =========================================================================
    (r"^SPCBEDB\d?$", r"sample\s*collection\s*category", "BE", "BECAT", "", False),
    (r"^SPCBEDB\d?$", r"sample\s*collection\s*method", "BE", "BEMETHOD", "", False),
    (r"^SPCBEDB\d?$", r"biofluid\s*collected", "BE", "BESPEC", "", False),
    (r"^SPCBEDB\d?$", r"biofluid\s*processed", "BE", "BEPRCSD", "", True),
    (r"^SPCBEDB\d?$", r"biofluid", "BE", "BESPEC", "", False),
    (r"^SPCBEDB\d?$", r"sample\s*format", "BE", "SMPFMT", "", True),
    (r"^SPCBEDB\d?$", r"sample\s*collected$", "BE", "BESTAT", "", False),
    (r"^SPCBEDB\d?$", r"^sample\s*id$", "BE", "BEREFID", "", False),
    (r"^SPCBEDB\d?$", r"^date\s*of\s*collection", "BE", "BEDTC", "", False),
    (r"^SPCBEDB\d?$", r"^time\s*of\s*collection", "BE", "BETM", "", True),
    (r"^SPCBEDB\d?$", r"^date\s*of\s*processing", "BE", "BEPRCDTC", "", True),
    (r"^SPCBEDB\d?$", r"^time\s*of\s*processing", "BE", "BEPRCTM", "", True),
    (r"^SPCBEDB\d?$", r"sample\s*condition,?\s*other", "BE", "SMPCDOT", "", True),
    (r"^SPCBEDB\d?$", r"sample\s*condition$", "BE", "SMPCOND", "", True),
    (r"^SPCBEDB\d?$", r"laboratory\s*id", "BE", "LABID", "", True),
    (r"^SPCBEDB\d?$", r"main\s*consent\s*category", "BE", "CNSTCAT", "", True),
    (r"^SPCBEDB\d?$", r"optional\s*consent.*category", "BE", "OCNSTCAT", "", True),
    (r"^SPCBEDB\d?$", r"destination\s*system", "BE", "DESTSYS", "", True),
    (r"^SPCBEDB\d?$", r"number\s*of\s*aliquots", "BE", "ALIQCNT", "", True),

    # =========================================================================
    # SPC: PK BLOOD → PC domain
    # =========================================================================
    (r"^SPCPKB$", r"sample\s*collection\s*category", "PC", "PCCAT", "", False),
    (r"^SPCPKB$", r"sample\s*collection\s*method", "PC", "PCMETHOD", "", True),
    (r"^SPCPKB$", r"biofluid\s*collected", "PC", "PCSPEC", "", False),
    (r"^SPCPKB$", r"biofluid\s*processed", "PC", "PCPRCSD", "", True),
    (r"^SPCPKB$", r"biofluid", "PC", "PCSPEC", "", False),
    (r"^SPCPKB$", r"sample\s*format", "PC", "SMPFMT", "", True),
    (r"^SPCPKB$", r"analysis\s*method", "PC", "PCTEST", "", False),
    (r"^SPCPKB$", r"sample\s*collected$", "PC", "PCSTAT", "", False),
    (r"^SPCPKB$", r"^sample\s*id$", "PC", "PCREFID", "", False),
    (r"^SPCPKB$", r"^date\s*of\s*collection", "PC", "PCDTC", "", False),
    (r"^SPCPKB$", r"^time\s*of\s*collection", "PC", "PCTM", "", True),
    (r"^SPCPKB$", r"^date\s*of\s*processing", "PC", "PCPRCDTC", "", True),
    (r"^SPCPKB$", r"^time\s*of\s*processing", "PC", "PCPRCTM", "", True),
    (r"^SPCPKB$", r"^number\s*of\s*aliquots$", "PC", "ALIQCNT", "", True),
    (r"^SPCPKB$", r"sample\s*condition,?\s*other", "PC", "SMPCDOT", "", True),
    (r"^SPCPKB$", r"sample\s*condition$", "PC", "SMPCOND", "", True),
    (r"^SPCPKB$", r"laboratory\s*id", "PC", "LABID", "", True),
    (r"^SPCPKB$", r"protocol\s*schedule", "PC", "PCTPT", "", False),

    # =========================================================================
    # SPC: ADA/nAb → IS domain (immunogenicity)
    # =========================================================================
    (r"^SPCPKB1$", r"sample\s*collection\s*category", "IS", "ISCAT", "", False),
    (r"^SPCPKB1$", r"sample\s*collected$", "IS", "ISSTAT", "", False),
    (r"^SPCPKB1$", r"^sample\s*id$", "IS", "ISREFID", "", False),
    (r"^SPCPKB1$", r"^date\s*of\s*collection", "IS", "ISDTC", "", False),
    (r"^SPCPKB1$", r"^time\s*of\s*collection", "IS", "ISTM", "", True),
    (r"^SPCPKB1$", r"protocol\s*schedule", "IS", "ISTPT", "", False),

    # =========================================================================
    # Generic SPC fallback
    # =========================================================================
    (r"^SPC", r"sample\s*collected$", "PC", "PCSTAT", "", False),
    (r"^SPC", r"^sample\s*id$", "PC", "PCREFID", "", False),
    (r"^SPC", r"^date\s*of\s*collection$", "PC", "PCDTC", "", False),
    (r"^SPC", r"^time\s*of\s*collection$", "PC", "PCTM", "", True),
    (r"^SPC", r"sample\s*collection\s*category", "PC", "PCCAT", "", False),
    (r"^SPC", r"biofluid\s*collected", "PC", "PCSPEC", "", False),
    (r"^SPC", r"biofluid", "PC", "PCSPEC", "", False),

    # =========================================================================
    # IE
    # =========================================================================
    (r"^IE\d?$", r"did\s*the\s*subject\s*meet\s*all\s*eligibility", "IE", "IEORALL", "", True),
    (r"^IE\d?$", r"were\s*all\s*eligibility\s*criteria\s*met", "IE", "IEORALL", "", True),
    (r"^IE\d?$", r"subject\s*complies\s*with\s*all\s*inclusion", "IE", "IEORALL", "", True),
    (r"^IE\d?$", r"category\s*of\s*failed\s*criterion", "IE", "IECAT", "", False),
    (r"^IE\d?$", r"^criterion\s*type$", "IE", "IECAT", "", False),
    (r"^IE\d?$", r"^failed\s*criterion\s*type$", "IE", "IECAT", "", False),
    (r"^IE\d?$", r"identifier\s*of\s*the\s*criterion", "IE", "IETESTCD", "", False),
    (r"^IE\d?$", r"exception\s*criterion\s*identifier", "IE", "IETESTCD", "", False),
    (r"^IE\d?$", r"^failed\s*criterion\s*no", "IE", "IETESTCD", "", False),
    (r"^IE\d?$", r"was\s*subject\s*allocated\s*a\s*randomization", "DS", "NOT_SUBMITTED", "", False),
    (r"^IE\d?$", r"randomization\s*code$", "DM", "ACTARMCD", "", False),
    (r"^IE\d?$", r"date\s*of\s*randomization", "DS", "DSSTDTC", "", False),
    (r"^IE\d?$", r"what\s*was\s*the\s*randomization\s*date", "DS", "DSSTDTC", "", False),
    (r"^IE\d?$", r"derived\s*critfno", "IE", "NOT_SUBMITTED", "", False),

    # =========================================================================
    # DS (Disposition)
    # =========================================================================
    (r"^DS\d?$", r"completion\s*or\s*discontinuation\s*date", "DS", "DSSTDTC", "", False),
    (r"^DS\d?$", r"subject.*status", "DS", "DSDECOD", "", False),
    (r"^DS\d?$", r"specify\s*status", "DS", "DSTERM", "", False),
    (r"^DS\d?$", r"date\s*of\s*main\s*informed\s*consent", "DS", "DSSTDTC", "", False),
    (r"^DS\d?$", r"main\s*informed\s*consent\s*obtained", "DS", "DSOCCUR", "", False),
    (r"^DS\d?$", r"csp\s*version", "DS", "DSSPFY", "", True),
    (r"^DS\d?$", r"re-?signed\s*informed\s*consent", "DS", "DSOCCUR", "", False),
    (r"^DS\d?$", r"date\s*of\s*re-?signed", "DS", "DSSTDTC", "", False),
    (r"^DS\d?$", r"date\s*of\s*informed\s*consent", "DS", "DSSTDTC", "", False),
    (r"^DS\d?$", r"optional\s*consent.*obtained", "DS", "DSOCCUR", "", False),
    (r"^DS\d?$", r"optional\s*consent.*category", "DS", "DSTERM", "", False),
    (r"^DS\d?$", r"informed\s*consent\s*withdrawal\s*date", "DS", "DSSTDTC", "", False),
    (r"^DS\d?$", r"what\s*was\s*the\s*protocol\s*milestone", "DS", "DSTERM", "", False),
    (r"^DS\d?$", r"consent\s*withdrawal\s*category", "DS", "DSSPFY", "", True),
    (r"^DS\d?$", r"other\s*status,?\s*specify", "DS", "DSMODIFY", "", False),

    # =========================================================================
    # SV (Subject Visits)
    # =========================================================================
    (r"^SV$", r"did\s*this\s*visit\s*occur", "SV", "SVOCCUR", "", False),
    (r"^SV$", r"^visit\s*date$", "SV", "SVSTDTC", "", False),
    (r"^SV$", r"^contact\s*mode$", "SV", "SVUPDES", "", True),
    (r"^SV$", r"^other.*specify$", "SV", "SVUPDES", "", True),

    # =========================================================================
    # AE (Adverse Events)
    # =========================================================================
    (r"^AE\d*$", r"^any\s*adverse\s*events?$", "AE", "AETERM", "", False),
    (r"^AE\d*$", r"^ae\s*no\.?$", "AE", "AESPID", "", False),
    (r"^AE\d*$", r"^adverse\s*event$", "AE", "AETERM", "", False),
    (r"^AE\d*$", r"date\s*ae\s*started", "AE", "AESTDTC", "", False),
    (r"^AE\d*$", r"date\s*ae\s*stopped", "AE", "AEENDTC", "", False),
    (r"^AE\d*$", r"^start\s*date$", "AE", "AESTDTC", "", False),
    (r"^AE\d*$", r"^end\s*date$", "AE", "AEENDTC", "", False),
    (r"^AE\d*$", r"were\s*any\s*adverse\s*events?\s*(experienced|reported)", "AE", "AEOCCUR", "", False),
    (r"^AE\d*$", r"^outcome$", "AE", "AEOUT", "", False),
    (r"^AE\d*$", r"maximum\s*ae\s*intensity", "AE", "AESEV", "", False),
    (r"^AE\d*$", r"^serious$", "AE", "AESER", "", False),
    (r"^AE\d*$", r"action\s*taken.*investigational\s*product", "AE", "AEACN", "", False),
    (r"^AE\d*$", r"causality.*investigational\s*product", "AE", "AEREL", "", False),
    (r"^AE\d*$", r"^adverse\s*event\s*category$", "AE", "AECAT", "", False),
    (r"^AE\d*$", r"medication\s*given\s*for\s*this\s*ae", "AE", "AECONTRT", "", False),

    # =========================================================================
    # CE (Clinical Events)
    # =========================================================================
    (r"^CE\d*$", r"has\s*the\s*patient\s*had\s*any.*exacerbation", "CE", "CEOCCUR", "", False),
    (r"^CE\d*$", r"was\s*there\s*any\s*new.*exacerbation", "CE", "CEOCCUR", "", False),
    (r"^CE\d*$", r"exacerbation.*start\s*date", "CE", "CESTDTC", "", False),
    (r"^CE\d*$", r"exacerbation.*end\s*date", "CE", "CEENDTC", "", False),
    (r"^CE\d*$", r"ongoing\s*at\s*study\s*end", "CE", "CEONGO", "", False),

    # =========================================================================
    # DD (Death Details)
    # =========================================================================
    (r"^DD\d*$", r"^death\s*date$", "DD", "DTHDT", "", False),
    (r"^DD\d*$", r"(?:primary\s*)?cause\s*of\s*death", "DD", "DTHCAUS", "", False),
    (r"^DD\d*$", r"related\s*to\s*disease\s*under", "DD", "DTHREL", "", True),

    # =========================================================================
    # PR (Procedures — additional)
    # =========================================================================
    (r"^PR\d*$", r"was\s*the\s*ncfbe\s*diagnosed", "PR", "PROCCUR", "", False),
    (r"^PR\d*$", r"ncfbe\s*diagnosis\s*date", "PR", "PRSTDTC", "", False),
    (r"^PR\d*$", r"diagnosis\s*basis", "PR", "PRMETHOD", "", False),
    (r"^PR\d*$", r"aetiologies", "PR", "PRSCAT", "", False),
    (r"^PR\d*$", r"microbiology.*pathogens", "PR", "PRTERM", "", False),

    # =========================================================================
    # HO (Healthcare Encounters)
    # =========================================================================
    (r"^HO\d*$", r"any\s*emergency\s*room\s*visit", "HO", "HOOCCUR", "", False),
    (r"^HO\d*$", r"any\s*unscheduled\s*physician\s*visit", "HO", "HOOCCUR", "", False),
    (r"^HO\d*$", r"was\s*unscheduled\s*visit\s*assessment\s*performed", "HO", "HOOCCUR", "", False),

    # =========================================================================
    # UNIVERSAL GATING QUESTIONS → NOT SUBMITTED
    # These fire on ANY form (use .* pattern). Place LAST so specific rules win.
    # =========================================================================
    (r".*", r"^was\s+the\s+child\s*bearing\s+potential", "XX", "NOT_SUBMITTED", "", False),
    (r".*", r"^is\s+the\s+(subject|patient)\s+(of\s+)?child\s*bearing", "XX", "NOT_SUBMITTED", "", False),
    (r".*", r"^if\s+(yes|no),?\s*(please\s+)?(complete|withdraw|fill|specify|provide|enter|report)", "XX", "NOT_SUBMITTED", "", False),
    (r".*", r"^if\s+medication\s+is\s+currently", "XX", "NOT_SUBMITTED", "", False),
    (r".*", r"^please\s+record\s+all\s+clinically", "XX", "NOT_SUBMITTED", "", False),
    (r".*", r"^please\s+report\s+medical\s+conditions", "XX", "NOT_SUBMITTED", "", False),
    (r".*", r"^if\s+yes,?\s*please\s+record", "XX", "NOT_SUBMITTED", "", False),
    (r".*", r"^total\s+number\s+of\s+exacerbations\s+(should|documented)\s+be\s+equal", "XX", "NOT_SUBMITTED", "", False),
]


# ============================================================================
# COMPILE RULES
# ============================================================================
_COMPILED_RULES: list[tuple[re.Pattern, re.Pattern, str, str, str, bool]] = [
    (re.compile(fp, re.IGNORECASE), re.compile(lp, re.IGNORECASE), d, v, c, s)
    for fp, lp, d, v, c, s in _RULES
]


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
        """Try matching compiled regex rules against form and label."""
        for form_re, label_re, domain, variable, codelist, is_supp in _COMPILED_RULES:
            if not form_re.match(form_to_match):
                continue
            if not label_re.search(norm_label):
                continue

            if variable == "NOT_SUBMITTED":
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
                if not check_usage(original_form_code, domain, variable, field_label):
                    continue

            return ResolutionResult(
                form_code=original_form_code,
                field_label=field_label,
                resolved=True,
                tier=ResolutionTier.TIER0_EXACT,
                confidence=confidence,
                sdtm_domain=domain,
                sdtm_variable=variable,
                sdtm_label="",
                core="",
                is_supplemental=is_supp,
                is_not_submitted=False,
                codelist_code=codelist,
            )
        return None

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