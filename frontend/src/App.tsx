import React, { useState, useCallback } from 'react';
import type { AnnotationStats } from './types';
import { uploadCRF } from './api';
import UploadView from './components/UploadView';
import ResultsView from './components/ResultsView';

type View =
  | { type: 'upload' }
  | { type: 'processing'; filename: string }
  | { type: 'results'; jobId: string; filename: string; stats: AnnotationStats };

export default function App() {
  const [view, setView] = useState<View>({ type: 'upload' });
  const [error, setError] = useState<string | null>(null);

  const handleUpload = useCallback(async (file: File) => {
    setError(null);
    setView({ type: 'processing', filename: file.name });
    try {
      const res = await uploadCRF(file);
      setView({ type: 'results', jobId: res.job_id, filename: res.filename, stats: res.stats });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
      setView({ type: 'upload' });
    }
  }, []);

  const handleReset = useCallback(() => {
    setView({ type: 'upload' });
    setError(null);
  }, []);

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 shadow-sm">
        <div className="max-w-screen-2xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded bg-blue-700 flex items-center justify-center text-white font-bold text-sm select-none">
              aCRF
            </div>
            <div>
              <span className="font-semibold text-slate-800 text-sm">SDTM Annotation Engine</span>
              <span className="ml-2 text-xs text-slate-400 hidden sm:inline">AstraZeneca</span>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {view.type === 'results' && (
              <span className="text-xs text-slate-500 hidden sm:block truncate max-w-48" title={view.filename}>
                {view.filename}
              </span>
            )}
            {view.type !== 'upload' && (
              <button
                onClick={handleReset}
                className="text-sm text-blue-600 hover:text-blue-800 font-medium transition-colors"
              >
                ← New Upload
              </button>
            )}
          </div>
        </div>
      </header>

      {/* Error banner */}
      {error && (
        <div className="bg-red-50 border-b border-red-200 px-6 py-3">
          <div className="max-w-screen-2xl mx-auto flex items-center gap-2 text-sm text-red-700">
            <span className="font-medium">Error:</span> {error}
            <button onClick={() => setError(null)} className="ml-auto text-red-500 hover:text-red-700">✕</button>
          </div>
        </div>
      )}

      {/* Main content */}
      <main className="max-w-screen-2xl mx-auto px-6 py-8">
        {view.type === 'upload' && <UploadView onUpload={handleUpload} />}
        {view.type === 'processing' && <ProcessingView filename={view.filename} />}
        {view.type === 'results' && (
          <ResultsView
            jobId={view.jobId}
            filename={view.filename}
            initialStats={view.stats}
          />
        )}
      </main>
    </div>
  );
}

function ProcessingView({ filename }: { filename: string }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6">
      <div className="w-16 h-16 border-4 border-blue-200 border-t-blue-700 rounded-full animate-spin" />
      <div className="text-center">
        <p className="text-slate-700 font-medium">Annotating CRF…</p>
        <p className="text-slate-500 text-sm mt-1 truncate max-w-sm" title={filename}>{filename}</p>
        <p className="text-slate-400 text-xs mt-3">Parsing fields → Resolving SDTM mappings → Writing annotations</p>
      </div>
    </div>
  );
}
