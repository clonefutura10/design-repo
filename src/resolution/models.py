"""
Resolution data models.

Defines the core data structures used throughout the SDTM resolution pipeline.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class ResolutionTier(str, Enum):
    """Which resolution strategy produced the result."""
    TIER0_EXACT = "tier0_exact"         # Deterministic rules / standards / spec
    TIER1_NOT_SUBMITTED = "tier1_ns"    # NOT SUBMITTED label match
    TIER2_SPEC = "tier2_spec"           # AZ spec fuzzy lookup
    TIER3_LLM = "tier3_llm"            # LLM-based resolution (future)
    UNRESOLVED = "unresolved"           # No resolution found


@dataclass
class ResolutionResult:
    """Result of a single field resolution attempt."""

    # Source identification
    form_code: str = ""
    field_label: str = ""

    # Resolution status
    resolved: bool = False
    tier: ResolutionTier = ResolutionTier.UNRESOLVED
    confidence: float = 0.0

    # SDTM mapping (primary)
    sdtm_domain: str = ""
    sdtm_variable: str = ""
    sdtm_label: str = ""
    core: str = ""

    # Flags
    is_supplemental: bool = False
    is_not_submitted: bool = False
    not_submitted_reason: str = ""

    # Codelist
    codelist_code: str = ""

    # Multi-mapping support: additional SDTM variables this field maps to
    # Each dict has keys: sdtm_domain, sdtm_variable, sdtm_label, is_supplemental
    additional_mappings: list[dict] = field(default_factory=list)

    # Findings domain TESTCD qualifier (e.g. 'VSTESTCD = "WEIGHT"')
    where_clause: str = ""

    # True if this variable is derived/assigned (not directly collected) — gets dashed border
    is_derived: bool = False

    # Value-level decode for controlled-response fields (issue #3).
    # e.g. 'Y = Yes, N = No' — rendered as a compact extra line under the
    # annotation. Empty for free-text / numeric / non-controlled fields.
    value_decode: str = ""

    @property
    def annotation_text(self) -> str:
        """
        Build the annotation string for PDF display (single-line format).
        Note: The pdf_writer uses _build_annotations_list() for separate boxes,
        but this property is useful for API responses and debugging.
        """
        if self.is_not_submitted:
            return "NOT SUBMITTED"
        if not self.sdtm_domain or not self.sdtm_variable:
            return ""

        # Primary mapping
        if self.is_supplemental:
            domain = self.sdtm_domain.upper()
            if domain.startswith("SUPP"):
                text = f"{domain}.{self.sdtm_variable}"
            else:
                text = f"SUPP{domain}.{self.sdtm_variable}"
        else:
            text = f"{self.sdtm_domain}.{self.sdtm_variable}"

        if self.codelist_code:
            text += f" ({self.codelist_code})"

        # Additional mappings (appended with " / " separator for text display)
        if self.additional_mappings:
            for mapping in self.additional_mappings:
                add_domain = mapping.get("sdtm_domain", "") or mapping.get("domain", "")
                add_variable = mapping.get("sdtm_variable", "") or mapping.get("variable", "")
                if add_domain and add_variable:
                    is_supp = mapping.get("is_supplemental", False) or mapping.get("is_supp", False)
                    if is_supp:
                        add_domain_upper = add_domain.upper()
                        if add_domain_upper.startswith("SUPP"):
                            add_text = f"{add_domain_upper}.{add_variable}"
                        else:
                            add_text = f"SUPP{add_domain_upper}.{add_variable}"
                    else:
                        add_text = f"{add_domain}.{add_variable}"
                    add_codelist = mapping.get("codelist_code", "") or mapping.get("codelist", "")
                    if add_codelist:
                        add_text += f" ({add_codelist})"
                    text += f" / {add_text}"

        return text

    @property
    def all_domains(self) -> list[str]:
        """
        Get all unique base domains this field maps to (primary + additional).
        Strips SUPP prefix to return base domain names.
        """
        domains = []
        if self.sdtm_domain:
            d = self.sdtm_domain.upper()
            if d.startswith("SUPP"):
                d = d[4:]
            if d:
                domains.append(d)

        if self.additional_mappings:
            for mapping in self.additional_mappings:
                d = mapping.get("sdtm_domain", "") or mapping.get("domain", "")
                d = d.upper()
                if d:
                    if d.startswith("SUPP"):
                        d = d[4:]
                    if d not in domains:
                        domains.append(d)

        return domains

    @property
    def has_multi_mappings(self) -> bool:
        """Check if this field has additional mappings beyond the primary."""
        return bool(self.additional_mappings)

    @property
    def total_mapping_count(self) -> int:
        """Total number of SDTM mappings (primary + additional)."""
        count = 1 if (self.sdtm_domain and self.sdtm_variable) else 0
        if self.additional_mappings:
            count += len(self.additional_mappings)
        return count