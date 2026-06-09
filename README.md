# aCRF Annotation Engine

Automated SDTM annotation tool for AstraZeneca blank CRF PDFs.  
Uploads a blank CRF PDF → resolves every field to its SDTM variable → writes a fully annotated aCRF PDF.

---

## Quick Start

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Add input files
Place the following proprietary files in `input/` (gitignored):
- `AZ_RAW_to_SDTM_Specification_Template.xlsx`
- `AZ_SDTM_CT.xlsx`
- `AZ_Corporate_SDTM_Standards.xlsx`
- `AZ_Map_Rule_Spec.xlsx`
- `blank_crf_clean.pdf`

### 3. Build the cache
```bash
python scripts/build_cache.py
```

### 4. Configure environment (optional)
```bash
cp .env.example .env
# Edit .env to set LLM endpoint if needed
```

### 5. Start the backend
```bash
python -m uvicorn app.main:app --reload --port 8000
```

### 6. Start the frontend
```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

---

## Architecture

```
blank_crf.pdf
     │
     ▼
┌─────────────────────┐
│  PDF Parsing Engine  │  PyMuPDF · form headers · field extraction · noise filter
└─────────────────────┘
     │
     ▼
┌──────────────────────────┐
│  Multi-tier Resolution   │
│  Tier 1 → NOT SUBMITTED  │  Gating triggers, non-collected fields
│  Tier 0-A → Regex Rules  │  Hardcoded per-form rules (confidence 0.98)
│  Tier 0-B → SDTM Std     │  CDISC formal labels (confidence 0.92)
│  Tier 0-C → AZ Spec      │  CRF-facing labels (confidence 0.90)
└──────────────────────────┘
     │
     ▼
┌──────────────────────────┐
│  PDF Annotation Writer   │  Domain colour-coding · bookmarks · overlap avoidance
└──────────────────────────┘
     │
     ▼
  aCRF_annotated.pdf
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/annotate` | Upload blank CRF PDF → run pipeline |
| `GET` | `/api/v1/annotate/{id}/download` | Download annotated PDF |
| `GET` | `/api/v1/annotate/{id}/stats` | Job statistics |
| `GET` | `/api/v1/annotate/{id}/details` | Full field mapping table |
| `GET` | `/api/v1/jobs` | List all jobs |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |

---

## Project Structure

```
├── app/                  FastAPI application
│   ├── main.py
│   ├── routers/
│   ├── schemas.py
│   └── services.py
├── src/                  Core pipeline
│   ├── pdf_parser/       PDF extraction
│   ├── resolution/       Tier 0/1 resolvers
│   ├── annotator/        PDF writer
│   └── utils/
├── config/               Settings & tokens
├── scripts/              build_cache.py
├── cache/                Generated JSON registries
├── input/                Input files (gitignored)
├── frontend/             React + TypeScript SPA
└── requirements.txt
```

---

## Environment Variables

See `.env.example`. All variables are optional for Tier 0/1 operation.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434` | LLM endpoint (Ollama/OpenAI-compat) |
| `LLM_MODEL_NAME` | `Qwen3-0.6B` | Model identifier |
| `LLM_API_KEY` | _(empty)_ | API key if required |

