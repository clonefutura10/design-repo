"""
Pydantic models for all cache data structures.

These schemas enforce type safety and provide clear documentation of what
each cache entry contains. Used for serialization (to JSON) and for
runtime type checking.
"""

from __future__ import annotations
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# AZ RAW-to-SDTM Specification
# ═══════════════════════════════════════════════════════════════════════════════

class SpecMappingEntry(BaseModel):
    """
    One mapping row from the AZ RAW-to-SDTM Specification.

    Represents: "Field X on form Y maps to SDTM variable Z in domain D."
    """

    # Source side (from CRF/RAW)
    library: str = Field(
        default="",
        description="Source library: 'RAW Corporate', 'RAW R&I', 'RAW Oncology', etc."
    )
    source_module: str = Field(
        description="RAW dataset/module = form code (e.g., 'AE', 'VS1', 'HEVENT')"
    )
    source_variable: str = Field(
        default="",
        description="RAW variable name (e.g., 'VSPERF') — not available on blank CRF"
    )
    source_label: str = Field(
        default="",
        description="RAW field label as it appears in the spec (primary matching key)"
    )
    source_label_normalized: str = Field(
        default="",
        description="Aggressively normalized label for exact dictionary lookup"
    )

    # Mapping logic
    map_definition: str = Field(
        default="",
        description="Natural language description of the mapping logic"
    )
    map_rule: str = Field(
        default="",
        description="Mapping function: COPY(), DECODE(), USUBJID(), etc."
    )
    map_order: str = Field(
        default="",
        description="Sequence order of this mapping within the module"
    )

    # Target side (SDTM)
    sdtm_domain: str = Field(
        default="",
        description="Target SDTM domain (e.g., 'AE', 'VS', 'SUPPAE')"
    )
    sdtm_variable: str = Field(
        default="",
        description="Target SDTM variable (e.g., 'AESTDTC', 'VSTEST')"
    )
    sdtm_label: str = Field(
        default="",
        description="SDTM variable label (e.g., 'Start Date/Time of Adverse Event')"
    )
    core: str = Field(
        default="",
        description="Core classification: Req, Perm, or empty (for SUPP)"
    )

    # Derived flags
    is_supplemental: bool = Field(
        default=False,
        description="True if target domain starts with 'SUPP'"
    )


class SpecModuleIndex(BaseModel):
    """
    Module-level index for quick domain resolution and filtering.

    Given a form code from the CRF, tells us which SDTM domain(s) it
    maps to and how many entries exist for it.
    """

    module_to_domains: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Module → list of SDTM domains (sorted)"
    )
    module_to_primary_domain: dict[str, str] = Field(
        default_factory=dict,
        description="Module → most frequent SDTM domain"
    )
    module_to_entry_count: dict[str, int] = Field(
        default_factory=dict,
        description="Module → total mapping entries"
    )
    module_to_label_count: dict[str, int] = Field(
        default_factory=dict,
        description="Module → entries with non-empty source labels"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FAISS Metadata
# ═══════════════════════════════════════════════════════════════════════════════

class SpecFAISSEntry(BaseModel):
    """Metadata for one vector in the AZ Spec FAISS index (Index 1)."""

    index_id: int
    source_module: str
    source_label: str
    source_label_normalized: str
    sdtm_domain: str
    sdtm_variable: str
    sdtm_label: str
    core: str
    is_supplemental: bool = False
    embedding_text: str = Field(description="Exact text that was embedded")


class CDISCFAISSEntry(BaseModel):
    """Metadata for one vector in the CDISC FAISS index (Index 2)."""

    index_id: int
    dataset: str
    variable: str
    label: str
    core: str = ""
    variable_type: str = ""
    role: str = ""
    dataset_level: str = ""
    embedding_text: str


# ═══════════════════════════════════════════════════════════════════════════════
# Controlled Terminology
# ═══════════════════════════════════════════════════════════════════════════════

class CTEntry(BaseModel):
    """
    Codelist association for a specific SDTM variable.

    Used to enrich annotations with codelist references: "VSTEST (C66742)".
    """

    sdtm_domain: str
    sdtm_variable: str
    sdtm_label: str = ""
    codelist: str = Field(description="Codelist identifier (e.g., 'VSTESTCD')")
    codelist_name: str = Field(default="", description="Full name of codelist")
    codelist_code: str = Field(default="", description="NCI code (e.g., 'C66742')")


# ═══════════════════════════════════════════════════════════════════════════════
# Corporate Standards
# ═══════════════════════════════════════════════════════════════════════════════

class CorporateDataset(BaseModel):
    """Domain definition from AZ Corporate Standards."""

    dataset: str
    label: str = ""
    layout: str = ""
    level: str = ""
    domain_class: str = ""
    description: str = ""


class CorporateVariable(BaseModel):
    """Variable definition from AZ Corporate Standards (used for FAISS Index 2)."""

    dataset: str
    variable: str
    label: str = ""
    variable_type: str = ""
    length: str = ""
    core: str = ""
    role: str = ""
    format_field: str = ""
    key: str = ""
    seq: str = ""
    origin: str = ""
    dataset_level: str = ""
    cdisc_notes: str = ""
    az_notes: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Map Rules (Reference Only)
# ═══════════════════════════════════════════════════════════════════════════════

class MapRule(BaseModel):
    """Mapping function definition (COPY, DECODE, etc.)."""

    function_name: str
    description: str = ""
    code: str = ""
    map_definition: str = ""