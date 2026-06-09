"""
Domain Inferencer — Study-Agnostic SDTM Domain Resolution.

Given a form_code and/or form_name extracted from any CRF header,
infers the most likely SDTM domain(s) using a multi-strategy scoring system.

This replaces hardcoded form_code → domain dictionaries with a generalizable
inference engine that handles:
  - Direct domain codes (DM, AE, VS)
  - Digit-suffixed codes (VS1, CM2, LB3)
  - Underscore-separated codes (LB_HEM, DS_ICF, SU_NIC)
  - Known variant codes (DEM→DM, PHYS→PE, HISM→MH, CRIT→IE)
  - Form name keyword matching ("Vital Signs" → VS)
  - Multi-domain forms (returns ranked list)

Design principle: NEVER return empty — always provide at least a best-guess
domain so downstream resolution has something to work with.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from functools import lru_cache

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DomainInference:
    """Result of domain inference for a single form."""
    domains: list[str]                # Ranked list of inferred domains (best first)
    confidence: float = 0.0           # Confidence in the primary (first) domain
    strategy: str = ""                # Which strategy produced the result
    form_code: str = ""               # Original form code
    form_name: str = ""               # Original form name (if available)

    @property
    def primary_domain(self) -> str:
        """The highest-confidence domain."""
        return self.domains[0] if self.domains else ""

    @property
    def is_multi_domain(self) -> bool:
        """Whether this form spans multiple SDTM domains."""
        return len(self.domains) > 1


# ═══════════════════════════════════════════════════════════════════════════════
# CDISC STANDARD DOMAINS (authoritative list)
# ═══════════════════════════════════════════════════════════════════════════════

_CDISC_DOMAINS: frozenset[str] = frozenset({
    # Special Purpose
    "DM", "CO", "SE", "SV", "SM",
    # Interventions
    "CM", "EC", "EX", "PR", "SU",
    # Events
    "AE", "CE", "DD", "DS", "DV", "HO", "MH",
    # Findings
    "BE", "BS", "CP", "CV", "DA", "EG", "FA", "FT", "GF",
    "IE", "IS", "LB", "MB", "MI", "MK", "MS", "NV",
    "OE", "PC", "PE", "PP", "QS", "RE", "RP", "RS",
    "SC", "SS", "TI", "TR", "TU", "UR", "VS",
    # Trial Design
    "TA", "TD", "TE", "TI", "TS", "TV",
    # Relationship
    "RELREC",
    # Associated Persons
    "APMH", "APSC",
})


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 1: KNOWN CODE VARIANTS
# Maps non-standard form codes to their SDTM domain(s).
# This is a curated list built from observing real AZ CRF exports.
# ═══════════════════════════════════════════════════════════════════════════════

_CODE_VARIANT_MAP: dict[str, list[str]] = {
    # Demographics variants
    "DEM": ["DM"],
    "DEMOG": ["DM"],

    # Physical Examination variants
    "PHYS": ["PE"],
    "PHYSF": ["PE"],
    "PHYSE": ["PE"],

    # Medical History variants
    "HISM": ["MH"],
    "MEDHIST": ["MH"],

    # Eligibility / Inclusion-Exclusion variants
    "CRIT": ["IE", "TI"],
    "ELIG": ["IE", "TI"],
    "INCEXC": ["IE", "TI"],

    # Substance Use variants
    "SUNIC": ["SU"],
    "SUALC": ["SU"],
    "SUTOB": ["SU"],
    "SUDRUG": ["SU"],

    # Consent / Disposition variants
    "ICFGEN": ["DS"],
    "CONSWD": ["DS"],

    # Oncology-specific forms → domains
    "ECOG": ["QS"],
    "PSTAT": ["QS"],
    "DISEXT": ["TU", "FA"],
    "CAPRX": ["CM"],
    "CAPXR": ["PR", "CM"],
    "CAPXROM": ["PR"],
    "PATHGEN": ["FA", "MH"],
    "PATHREP": ["FA"],
    "ECHOC": ["EG"],

    # Respiratory-specific forms
    "RESHISTE": ["MH", "FA"],
    "PULMTE": ["RE", "FA"],
    "FENO": ["RE", "FA"],
    "ALLERH": ["MH", "IS"],
    "EXACATE": ["CE", "AE"],
    "EXACD": ["CE"],
    "HEVENT": ["HO", "FA", "CE"],

    # Enrolment / Assignment
    "ENROL": ["DM"],
    "GROUP": ["DM"],

    # Infusion forms
    "INFSS": ["EX", "EC", "AE"],
    "INFDI": ["EX", "EC"],
    "INFRF": ["AE", "FA"],

    # Pregnancy
    "PREG": ["RP", "LB"],
    "PREGREP": ["RP"],

    # Sample collection
    "SPCBEDB": ["BE", "IS"],
    "SPCGIB": ["BE"],
    "SPCPKB": ["PC"],

    # Lab variants
    "CHEM": ["LB"],
    "HEM": ["LB"],
    "HEMAT": ["LB"],
    "URIN": ["LB"],
    "COAG": ["LB"],
    "THYROID": ["LB"],

    # Visit / scheduling
    "VISITP": ["SV"],
    "UNSVIS": ["SV"],

    # Dose / Exposure
    "DOSDISC": ["DS", "EX"],
    "OVERDOSE": ["AE", "EX"],

    # Questionnaires
    "ACQ": ["QS"],
    "ACQ5": ["QS"],
    "AQLQ": ["QS"],
    "EQ5D": ["QS"],
    "EQ5D5L": ["QS"],
    "SNOT": ["QS"],
    "SNOT22": ["QS"],
    "WPAI": ["QS"],
    "CGIC": ["QS"],

    # Assessment Performed
    "ASMPERF": ["PR"],

    # Death
    "DEATHEVT": ["DD"],

    # Liver safety
    "LIVERSS": ["LB", "AE"],
    "LIVERDI": ["LB", "AE"],

    # Contact
    "CONTACT": ["SV", "DS"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 3: FORM NAME → DOMAIN KEYWORD MATCHING
# Each domain has a set of keywords that strongly indicate it.
# Matched against normalized form name.
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _DomainKeywordRule:
    """A keyword matching rule for domain inference from form name."""
    domain: str
    keywords: list[str]            # Any match → this domain
    negative_keywords: list[str] = field(default_factory=list)  # Presence disqualifies
    weight: float = 0.85           # Base confidence for keyword match


_DOMAIN_NAME_RULES: list[_DomainKeywordRule] = [
    # Demographics
    _DomainKeywordRule(
        domain="DM",
        keywords=["demograph", "date of birth", "sex", "race", "ethnicity", "enrolment"],
        negative_keywords=["disease"],
    ),
    # Vital Signs
    _DomainKeywordRule(
        domain="VS",
        keywords=["vital sign", "blood pressure", "pulse", "heart rate"],
    ),
    # Adverse Events
    _DomainKeywordRule(
        domain="AE",
        keywords=["adverse event", "adverse experience"],
        negative_keywords=["serious"],
    ),
    _DomainKeywordRule(
        domain="AE",
        keywords=["serious adverse"],
        weight=0.90,
    ),
    # Medical History
    _DomainKeywordRule(
        domain="MH",
        keywords=[
            "medical history", "medical condition",
            "surgical history", "disease history",
            "allergy history", "respiratory disease history",
        ],
    ),
    # Concomitant Medications
    _DomainKeywordRule(
        domain="CM",
        keywords=[
            "medication", "concomitant", "prior biologic",
            "cancer therapy", "prior therap", "pharmacotherapy",
        ],
        negative_keywords=["current medication"],  # This is a sub-field, not a form
    ),
    # Procedures
    _DomainKeywordRule(
        domain="PR",
        keywords=[
            "physical exam", "surgery", "surgical",
            "procedure", "assessment performed", "radiotherapy",
        ],
    ),
    # ECG
    _DomainKeywordRule(
        domain="EG",
        keywords=["ecg", "electrocardiogram", "echo", "muga"],
    ),
    # Disposition
    _DomainKeywordRule(
        domain="DS",
        keywords=[
            "informed consent", "disposition", "study termination",
            "study completion", "withdrawal", "discontinu",
        ],
    ),
    # Subject Visits
    _DomainKeywordRule(
        domain="SV",
        keywords=["visit date", "unscheduled visit", "visit not done"],
    ),
    # Lab
    _DomainKeywordRule(
        domain="LB",
        keywords=[
            "laboratory", "lab test", "hematology", "chemistry",
            "urinalysis", "coagulation", "thyroid", "serum",
            "pregnancy test",
        ],
    ),
    # Inclusion/Exclusion
    _DomainKeywordRule(
        domain="IE",
        keywords=[
            "inclusion", "exclusion", "eligibility",
            "inclusion/exclusion", "criteria not met",
        ],
    ),
    # Substance Use
    _DomainKeywordRule(
        domain="SU",
        keywords=["substance use", "nicotine", "smoking", "alcohol use"],
    ),
    # Questionnaires
    _DomainKeywordRule(
        domain="QS",
        keywords=[
            "questionnaire", "performance status", "ecog",
            "quality of life", "patient reported", "pro ",
            "acq", "aqlq", "eq-5d", "eq5d", "snot", "wpai",
        ],
    ),
    # Tumor / Oncology
    _DomainKeywordRule(
        domain="TU",
        keywords=["tumor", "tumour", "extent of disease", "lesion"],
    ),
    # Findings About
    _DomainKeywordRule(
        domain="FA",
        keywords=["pathology", "findings about", "biopsy result"],
    ),
    # Clinical Events
    _DomainKeywordRule(
        domain="CE",
        keywords=["exacerbation", "clinical event", "flare"],
    ),
    # Healthcare Encounters
    _DomainKeywordRule(
        domain="HO",
        keywords=["hospitali", "healthcare encounter", "emergency room"],
    ),
    # Reproductive
    _DomainKeywordRule(
        domain="RP",
        keywords=["pregnancy", "reproductive", "child bearing"],
        negative_keywords=["pregnancy test"],  # Pregnancy test → LB
    ),
    # Death Details
    _DomainKeywordRule(
        domain="DD",
        keywords=["death detail", "cause of death"],
    ),
    # Exposure
    _DomainKeywordRule(
        domain="EX",
        keywords=["exposure", "dose", "infusion", "study drug admin"],
        negative_keywords=["radiotherapy"],
    ),
    # Biospecimen Events
    _DomainKeywordRule(
        domain="BE",
        keywords=["sample collection", "biological sample", "specimen"],
        negative_keywords=["pk", "pharmacokinetic"],
    ),
    # PK
    _DomainKeywordRule(
        domain="PC",
        keywords=["pharmacokinetic", "pk sample", "pk,"],
    ),
    # Microbiology
    _DomainKeywordRule(
        domain="MB",
        keywords=["microbiology", "culture", "pathogen"],
    ),
    # Subject Characteristics
    _DomainKeywordRule(
        domain="SC",
        keywords=["subject characteristic"],
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 2: CODE STRUCTURAL ANALYSIS
# Parses the form code itself for domain signals.
# ═══════════════════════════════════════════════════════════════════════════════

# Patterns for splitting form codes
_RE_TRAILING_DIGITS = re.compile(r"^([A-Z]+)\d+$")
_RE_UNDERSCORE_PREFIX = re.compile(r"^([A-Z]{2,4})_(.+)$")
_RE_LEADING_DOMAIN = re.compile(r"^([A-Z]{2})")


def _analyze_code_structure(form_code: str) -> list[tuple[str, float]]:
    """
    Analyze form code structure to extract domain hints.
    
    Returns list of (domain_candidate, confidence) tuples.
    """
    fc = form_code.upper().strip()
    if not fc:
        return []

    candidates: list[tuple[str, float]] = []

    # Direct CDISC domain match (highest confidence)
    if fc in _CDISC_DOMAINS:
        candidates.append((fc, 1.0))
        return candidates  # No need to try other strategies

    # Known variant (high confidence)
    if fc in _CODE_VARIANT_MAP:
        for domain in _CODE_VARIANT_MAP[fc]:
            candidates.append((domain, 0.93))
        return candidates

    # Strip trailing digits: "VS1" → "VS", "CM2" → "CM", "LB3" → "LB"
    digit_match = _RE_TRAILING_DIGITS.match(fc)
    if digit_match:
        base = digit_match.group(1)
        if base in _CDISC_DOMAINS:
            candidates.append((base, 0.95))
            return candidates
        # Check variant map with base
        if base in _CODE_VARIANT_MAP:
            for domain in _CODE_VARIANT_MAP[base]:
                candidates.append((domain, 0.90))
            return candidates

    # Underscore-separated: "LB_HEM" → "LB", "DS_ICF" → "DS", "SU_NIC" → "SU"
    underscore_match = _RE_UNDERSCORE_PREFIX.match(fc)
    if underscore_match:
        prefix = underscore_match.group(1)
        suffix = underscore_match.group(2)
        if prefix in _CDISC_DOMAINS:
            candidates.append((prefix, 0.93))
            return candidates
        # Check if suffix is informative (e.g., "NIC" for nicotine → SU)
        if prefix in _CODE_VARIANT_MAP:
            for domain in _CODE_VARIANT_MAP[prefix]:
                candidates.append((domain, 0.88))
            return candidates

    # Check if code STARTS WITH a 2-letter domain
    if len(fc) > 2:
        leading = fc[:2]
        if leading in _CDISC_DOMAINS:
            candidates.append((leading, 0.70))

    # Check full code in variant map (catches things like "SUNIC", "SUALC")
    if fc in _CODE_VARIANT_MAP:
        for domain in _CODE_VARIANT_MAP[fc]:
            candidates.append((domain, 0.88))

    return candidates


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 3: FORM NAME KEYWORD MATCHING
# ═══════════════════════════════════════════════════════════════════════════════

def _match_form_name(form_name: str) -> list[tuple[str, float]]:
    """
    Match form name against domain keyword rules.
    
    Returns list of (domain, confidence) tuples for all matching domains.
    """
    if not form_name:
        return []

    name_lower = form_name.lower().strip()
    matches: list[tuple[str, float]] = []

    for rule in _DOMAIN_NAME_RULES:
        # Check negative keywords first (disqualifiers)
        if any(neg in name_lower for neg in rule.negative_keywords):
            continue

        # Check positive keywords
        for keyword in rule.keywords:
            if keyword in name_lower:
                matches.append((rule.domain, rule.weight))
                break  # One keyword match per rule is enough

    return matches


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN INFERENCE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def infer_domains(
    form_code: str,
    form_name: str = "",
) -> DomainInference:
    """
    Infer SDTM domain(s) for a CRF form using multiple strategies.
    
    Strategy priority:
      1. Code IS a CDISC domain (confidence 1.0)
      2. Code structural analysis — digit stripping, underscore splitting (0.93-0.95)
      3. Known code variant lookup (0.88-0.93)
      4. Form name keyword matching (0.85)
      5. Combination of code + name signals (0.70-0.85)
    
    Args:
        form_code: Form code extracted from CRF header (e.g., "DEM", "LB_HEM")
        form_name: Form name extracted from CRF header (e.g., "Demography")
    
    Returns:
        DomainInference with ranked domains and confidence.
    """
    fc = form_code.upper().strip() if form_code else ""

    # Collect all signals
    code_signals = _analyze_code_structure(fc) if fc else []
    name_signals = _match_form_name(form_name) if form_name else []

    # Merge and rank signals
    domain_scores: dict[str, float] = {}
    domain_strategies: dict[str, str] = {}

    # Code signals (higher priority)
    for domain, conf in code_signals:
        if domain not in domain_scores or conf > domain_scores[domain]:
            domain_scores[domain] = conf
            domain_strategies[domain] = "code_analysis"

    # Name signals (additive if code also matched, otherwise standalone)
    for domain, conf in name_signals:
        if domain in domain_scores:
            # Boost existing score (code + name agreement)
            boost = min(conf * 0.1, 0.05)  # Small boost for confirmation
            domain_scores[domain] = min(domain_scores[domain] + boost, 1.0)
            domain_strategies[domain] = "code+name"
        else:
            domain_scores[domain] = conf
            domain_strategies[domain] = "name_keywords"

    # If still nothing, try last-resort heuristics
    if not domain_scores and fc:
        # Use first 2 chars as domain guess (very low confidence)
        if len(fc) >= 2:
            guess = fc[:2]
            if guess in _CDISC_DOMAINS:
                domain_scores[guess] = 0.50
                domain_strategies[guess] = "prefix_guess"

    # Build result
    if not domain_scores:
        logger.debug(
            f"Domain inference failed: form_code='{form_code}', form_name='{form_name}'"
        )
        return DomainInference(
            domains=[],
            confidence=0.0,
            strategy="none",
            form_code=form_code,
            form_name=form_name,
        )

    # Sort by confidence (descending)
    ranked = sorted(domain_scores.items(), key=lambda x: -x[1])
    domains = [d for d, _ in ranked]
    top_confidence = ranked[0][1]
    top_strategy = domain_strategies[ranked[0][0]]

    result = DomainInference(
        domains=domains,
        confidence=top_confidence,
        strategy=top_strategy,
        form_code=form_code,
        form_name=form_name,
    )

    logger.debug(
        f"Domain inference: {form_code} ({form_name}) → {domains} "
        f"[{top_strategy}, conf={top_confidence:.2f}]"
    )

    return result


def get_form_regex_for_domain(domain: str) -> re.Pattern:
    """
    Build a regex pattern that matches form codes belonging to a domain.
    
    Used by tier0 to re-check rules against inferred domain.
    E.g., domain="DM" → pattern matches "DM", "DM1", "DEM", "DEMOG", etc.
    """
    domain_upper = domain.upper()

    # Collect all codes that map to this domain
    matching_codes = {domain_upper}  # The domain itself

    # Add digit variants
    matching_codes.add(f"{domain_upper}\\d*")

    # Add known variants that map to this domain
    for code, domains in _CODE_VARIANT_MAP.items():
        if domain_upper in domains:
            matching_codes.add(re.escape(code))

    # Build combined pattern
    pattern_str = "|".join(f"^{p}$" for p in sorted(matching_codes))
    return re.compile(pattern_str, re.IGNORECASE)


@lru_cache(maxsize=256)
def infer_domains_cached(form_code: str, form_name: str = "") -> DomainInference:
    """Cached version of infer_domains for repeated lookups."""
    return infer_domains(form_code, form_name)