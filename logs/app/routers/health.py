"""Health check."""

from fastapi import APIRouter
from pathlib import Path

router = APIRouter()


@router.get("/health")
async def health():
    """Service health check with cache status."""
    cache_files = {
        "sdtm_standards": Path("cache/sdtm_spec_by_dataset.json").exists(),
        "az_spec_lookup": Path("cache/az_spec_lookup.json").exists(),
        "not_submitted_labels": Path("cache/sdtm_not_submitted_labels.json").exists(),
    }
    all_ok = all(cache_files.values())

    return {
        "status": "healthy" if all_ok else "degraded",
        "service": "acrf-automation",
        "version": "1.0.0",
        "cache": cache_files,
    }