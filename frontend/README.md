# aCRF Annotator — React Frontend


```bash
# 1. Start the FastAPI backend (from project root)
uvicorn app.main:app --reload

# 2. Start the React dev server (proxies /api to :8000)
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

## Production Build

```bash
cd frontend
npm run build
# FastAPI auto-serves frontend/dist/ at http://localhost:8000
```

## Project Structure

```
frontend/src/
├── api/
│   ├── client.ts      # Axios API functions
│   └── types.ts       # TypeScript types (mirrors Pydantic schemas)
├── components/
│   ├── Layout.tsx     # Sidebar + top bar
│   ├── StatCard.tsx   # Metric card
│   └── Badges.tsx     # Domain/tier colour badges
└── pages/
    ├── UploadPage.tsx    # Drag-drop upload
    ├── JobsPage.tsx      # Job history table
    └── JobDetailPage.tsx # Stats + mappings table
```
