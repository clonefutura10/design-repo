"""Pydantic request/response models for the API."""

from __future__ import annotations
from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class AnnotationStats(BaseModel):
    """Pipeline run statistics."""
    total_pages: int = Field(description="Total pages in source PDF")
    total_fields_extracted: int = Field(description="Raw fields found by parser")
    unique_forms: int = Field(description="Distinct CRF form types")
    fields_after_noise_filter: int = Field(description="Fields after removing noise")
    noise_removed: int = Field(description="Noise fields filtered out")
    resolved_count: int = Field(description="Fields successfully mapped to SDTM")
    unresolved_count: int = Field(description="Fields that could not be mapped")
    resolution_rate: float = Field(description="Percentage resolved")
    annotations_written: int = Field(description="Annotations placed on PDF")
    pages_annotated: int = Field(description="Pages that received annotations")
    not_submitted_count: int = Field(description="Fields marked NOT SUBMITTED")
    duplicates_skipped: int = Field(description="Duplicate annotations suppressed")
    skipped_no_position: int = Field(description="Fields with no PDF position")
    tier0_regex: int = Field(description="Resolved by hardcoded regex rules")
    tier0_standards: int = Field(description="Resolved by SDTM standards lookup")
    tier0_az_spec: int = Field(description="Resolved by AZ spec lookup")


class AnnotationResponse(BaseModel):
    """Response after annotation completes."""
    job_id: str
    status: JobStatus
    stats: AnnotationStats
    message: str
    filename: str


class FieldMapping(BaseModel):
    """Single CRF field → SDTM mapping."""
    form_code: str
    field_label: str
    annotation: str = Field(description="Primary annotation string (e.g. 'VS.VSORRES')")
    additional_annotations: List[str] = Field(
        default_factory=list,
        description="Additional annotations for multi-variable fields",
    )
    sdtm_domain: Optional[str] = None
    sdtm_variable: Optional[str] = None
    codelist_code: Optional[str] = None
    is_supplemental: bool = False
    is_not_submitted: bool = False
    confidence: float = 0.0
    tier: str = "unresolved"


class AnnotationOverride(BaseModel):
    """User-supplied annotation override for a single field."""
    form_code: str
    field_label: str
    annotations: List[str] = Field(
        description=(
            "Annotation strings, e.g. ['VS.VSORRES', 'SUPPVS.QVAL']. "
            "Empty list = delete (mark unresolved). "
            "['NOT SUBMITTED'] = mark as not submitted."
        )
    )


class EditRequest(BaseModel):
    """Batch of annotation overrides to apply."""
    overrides: List[AnnotationOverride]


class EditResponse(BaseModel):
    """Result after applying annotation overrides and regenerating the PDF."""
    job_id: str
    message: str
    changes_applied: int
    stats: AnnotationStats


class AnnotationDetail(BaseModel):
    """Full mapping breakdown for a job."""
    job_id: str
    total_mappings: int
    resolved_count: int
    unresolved_count: int
    resolved: List[FieldMapping]
    unresolved: List[FieldMapping]


class JobSummary(BaseModel):
    """Brief job info for listing."""
    job_id: str
    status: JobStatus
    filename: str
    annotations_written: int
    resolution_rate: float


class ErrorResponse(BaseModel):
    """Error detail."""
    detail: str