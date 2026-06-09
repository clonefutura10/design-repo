"""
Annotation endpoints.

POST /annotate            → Upload PDF, run pipeline, return stats
GET  /annotate/{id}/download → Download annotated PDF
GET  /annotate/{id}/stats    → Re-fetch stats
GET  /annotate/{id}/details  → Full mapping table
GET  /jobs                   → List all jobs
"""

from __future__ import annotations
import tempfile
from pathlib import Path
from typing import List

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse


from fastapi import APIRouter, UploadFile, File, HTTPException, Body

from app.schemas import (
    AnnotationResponse,
    AnnotationDetail,
    AnnotationStats,
    FieldMapping,
    JobStatus,
    JobSummary,
    EditRequest,
    EditResponse,
)
from app.services import run_pipeline, get_job, list_jobs, apply_edits

router = APIRouter()


@router.post("/annotate", response_model=AnnotationResponse)
async def annotate_crf(
    file: UploadFile = File(..., description="Blank CRF PDF file"),
):
    """
    Upload a blank CRF PDF and run the SDTM annotation pipeline.

    Returns job ID, statistics, and a download link for the annotated PDF.
    """
    # ── Validate ──
    filename = file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, detail="Only PDF files are accepted")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(400, detail="Uploaded file is empty")
    if len(content) > 150 * 1024 * 1024:
        raise HTTPException(400, detail="File exceeds 150MB limit")

    # ── Save to temp ──
    tmp_dir = Path(tempfile.mkdtemp(prefix="acrf_in_"))
    input_path = tmp_dir / "input.pdf"
    input_path.write_bytes(content)

    # ── Run pipeline ──
    try:
        result = run_pipeline(input_path, original_filename=filename)
    except Exception as e:
        raise HTTPException(500, detail=f"Pipeline error: {str(e)}")

    stats = AnnotationStats(**result.stats)

    return AnnotationResponse(
        job_id=result.job_id,
        status=JobStatus.COMPLETED,
        stats=stats,
        filename=filename,
        message=(
            f"Annotated {stats.annotations_written} fields across "
            f"{stats.pages_annotated} pages "
            f"({stats.resolution_rate}% resolution rate)"
        ),
    )


@router.get("/annotate/{job_id}/download")
async def download_pdf(job_id: str):
    """Download the annotated aCRF PDF."""
    result = get_job(job_id)
    if not result:
        raise HTTPException(404, detail=f"Job '{job_id}' not found")
    if not result.output_pdf_path.exists():
        raise HTTPException(410, detail="Output file expired or deleted")

    return FileResponse(
        path=str(result.output_pdf_path),
        media_type="application/pdf",
        filename=f"aCRF_annotated_{job_id}.pdf",
    )


@router.get("/annotate/{job_id}/stats", response_model=AnnotationResponse)
async def get_stats(job_id: str):
    """Retrieve statistics for a completed job."""
    result = get_job(job_id)
    if not result:
        raise HTTPException(404, detail=f"Job '{job_id}' not found")

    return AnnotationResponse(
        job_id=result.job_id,
        status=JobStatus.COMPLETED,
        stats=AnnotationStats(**result.stats),
        filename=result.filename,
        message="Job completed",
    )


@router.get("/annotate/{job_id}/details", response_model=AnnotationDetail)
async def get_details(job_id: str):
    """
    Full mapping details — every field with its SDTM assignment.

    Frontend can render this as a searchable/filterable table.
    """
    result = get_job(job_id)
    if not result:
        raise HTTPException(404, detail=f"Job '{job_id}' not found")

    def _build_annotation_text(r: object) -> str:
        if r.is_not_submitted:
            return "NOT SUBMITTED"
        if not r.sdtm_domain or not r.sdtm_variable:
            return ""
        prefix = f"SUPP{r.sdtm_domain}" if r.is_supplemental else r.sdtm_domain
        text = f"{prefix}.{r.sdtm_variable}"
        if r.codelist_code:
            text += f" ({r.codelist_code})"
        return text

    def _extra_annotations(r: object) -> list[str]:
        """Build annotation strings for additional_mappings."""
        extras = []
        for m in getattr(r, "additional_mappings", None) or []:
            d = (m.get("domain") or m.get("sdtm_domain", "")).upper()
            v = m.get("variable") or m.get("sdtm_variable", "")
            if not d or not v:
                continue
            is_supp = m.get("is_supp", False) or m.get("is_supplemental", False)
            prefix = f"SUPP{d}" if is_supp else d
            text = f"{prefix}.{v}"
            cl = m.get("codelist_code", "") or m.get("codelist", "")
            if cl:
                text += f" ({cl})"
            extras.append(text)
        return extras

    resolved = [
        FieldMapping(
            form_code=r.form_code,
            field_label=r.field_label,
            annotation=_build_annotation_text(r),
            additional_annotations=_extra_annotations(r),
            sdtm_domain=r.sdtm_domain or None,
            sdtm_variable=r.sdtm_variable or None,
            codelist_code=r.codelist_code or None,
            is_supplemental=r.is_supplemental,
            is_not_submitted=r.is_not_submitted,
            confidence=r.confidence,
            tier=r.tier.value if r.tier else "unresolved",
        )
        for r in result.resolved_results
    ]

    unresolved = [
        FieldMapping(
            form_code=r.form_code,
            field_label=r.field_label,
            annotation="",
            sdtm_domain=None,
            sdtm_variable=None,
            codelist_code=None,
            is_supplemental=False,
            is_not_submitted=False,
            confidence=0.0,
            tier="unresolved",
        )
        for r in result.unresolved_results
    ]

    return AnnotationDetail(
        job_id=result.job_id,
        total_mappings=len(resolved) + len(unresolved),
        resolved_count=len(resolved),
        unresolved_count=len(unresolved),
        resolved=resolved,
        unresolved=unresolved,
    )


@router.post("/annotate/{job_id}/edit", response_model=EditResponse)
async def edit_annotations(job_id: str, request: EditRequest = Body(...)):
    """
    Apply user annotation overrides and regenerate the annotated PDF.

    Each override specifies a field by (form_code, field_label) and provides
    a new list of annotation strings.  The PDF is re-rendered immediately.

    Annotation string formats accepted:
      - "VS.VSORRES"          → standard variable
      - "SUPPVS.QVAL"         → supplemental variable
      - "VS.VSORRES (C66770)" → with codelist
      - "NOT SUBMITTED"       → mark field as not collected
      - [] (empty list)       → remove annotation (mark unresolved)
    """
    if not request.overrides:
        raise HTTPException(400, detail="No overrides provided")

    job = get_job(job_id)
    if not job:
        raise HTTPException(404, detail=f"Job '{job_id}' not found")
    if not job.data_fields:
        raise HTTPException(409, detail="Job has no stored field data — cannot re-annotate")

    try:
        updated = apply_edits(job_id, request.overrides)
    except ValueError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail=f"Re-annotation failed: {str(e)}")

    changes = len(request.overrides)
    return EditResponse(
        job_id=job_id,
        message=f"Applied {changes} override(s) and regenerated PDF successfully.",
        changes_applied=changes,
        stats=AnnotationStats(**updated.stats),
    )


@router.get("/jobs", response_model=List[JobSummary])
async def get_jobs():
    """List all annotation jobs."""
    jobs = list_jobs()
    return [
        JobSummary(
            job_id=j["job_id"],
            status=JobStatus.COMPLETED,
            filename=j["filename"],
            annotations_written=j["annotations_written"],
            resolution_rate=j["resolution_rate"],
        )
        for j in jobs
    ]