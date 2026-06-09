"""
aCRF Automation API.

Endpoints:
    POST /api/v1/annotate          → Upload blank CRF, get annotated PDF + stats
    GET  /api/v1/annotate/{id}/download → Download annotated PDF
    GET  /api/v1/annotate/{id}/stats    → Get job statistics
    GET  /api/v1/annotate/{id}/details  → Get all field mappings
    GET  /api/v1/jobs                   → List all jobs
    GET  /health                        → Health check
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.routers import annotate, health

app = FastAPI(
    title="aCRF Automation API",
    description=(
        "Automated SDTM annotation engine for blank CRF PDFs. "
        "Upload a CRF and receive a fully annotated aCRF with SDTM mappings."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["Health"])
app.include_router(annotate.router, prefix="/api/v1", tags=["Annotation"])

# ── Serve React frontend (production) ──────────────────────────────────────
_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        """Catch-all: serve React SPA for any non-API route."""
        file = _FRONTEND_DIST / full_path
        if file.exists() and file.is_file():
            return FileResponse(str(file))
        return FileResponse(str(_FRONTEND_DIST / "index.html"))


@app.on_event("startup")
async def startup_event():
    """Verify cache files exist on startup."""
    required = [
        "cache/sdtm_spec_by_dataset.json",
        "cache/az_spec_lookup.json",
        "cache/sdtm_not_submitted_labels.json",
    ]
    missing = [f for f in required if not Path(f).exists()]
    if missing:
        import logging
        logging.warning(f"Missing cache files (run scripts/build_cache.py): {missing}")