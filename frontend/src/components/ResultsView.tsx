import React, { useEffect, useState, useCallback } from 'react';
import type { AnnotationStats, AnnotationDetail, EditRequest } from '../types';
import { getDetails, applyEdits, downloadUrl } from '../api';
import StatsCards from './StatsCards';
import AnnotationTable from './AnnotationTable';

interface Props {
  jobId: string;
  filename: string;
  initialStats: AnnotationStats;
}

export default function ResultsView({ jobId, filename, initialStats }: Props) {
  const [stats, setStats] = useState<AnnotationStats>(initialStats);
  const [detail, setDetail] = useState<AnnotationDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(true);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [regenMessage, setRegenMessage] = useState<string | null>(null);

  useEffect(() => {
    setLoadingDetail(true);
    setDetailError(null);
    getDetails(jobId)
      .then(setDetail)
      .catch(e => setDetailError(e instanceof Error ? e.message : 'Failed to load details'))
      .finally(() => setLoadingDetail(false));
  }, [jobId]);

  const handleRegenerate = useCallback(async (req: EditRequest) => {
    setRegenerating(true);
    setRegenMessage(null);
    try {
      const res = await applyEdits(jobId, req);
      setStats(res.stats);
      setRegenMessage(`PDF regenerated — ${res.changes_applied} field${res.changes_applied !== 1 ? 's' : ''} updated.`);
    } catch (e) {
      setRegenMessage(`Error: ${e instanceof Error ? e.message : 'Regeneration failed'}`);
    } finally {
      setRegenerating(false);
    }
  }, [jobId]);

  return (
    <div className="flex flex-col gap-6">
      {/* Stats row */}
      <StatsCards stats={stats} />

      {/* Regen success / error banner */}
      {regenMessage && (
        <div className={`rounded-lg border px-4 py-3 text-sm flex items-center justify-between
          ${regenMessage.startsWith('Error')
            ? 'bg-red-50 border-red-200 text-red-700'
            : 'bg-green-50 border-green-200 text-green-700'}`}>
          <span>{regenMessage}</span>
          <button onClick={() => setRegenMessage(null)} className="ml-4 opacity-60 hover:opacity-100">✕</button>
        </div>
      )}

      {/* Download bar */}
      <div className="flex items-center justify-between bg-white border border-slate-200 rounded-xl px-5 py-3 shadow-sm">
        <div>
          <p className="text-sm font-semibold text-slate-800">Annotated CRF ready</p>
          <p className="text-xs text-slate-500 mt-0.5">
            {stats.annotations_written} annotations · {stats.pages_annotated} pages · {stats.resolution_rate}% resolution
          </p>
        </div>
        <a
          href={downloadUrl(jobId)}
          download={`aCRF_${filename}`}
          className="px-5 py-2.5 bg-blue-700 text-white text-sm font-semibold rounded-lg hover:bg-blue-800 transition-colors shadow-sm flex items-center gap-2"
        >
          <span>⬇</span> Download PDF
        </a>
      </div>

      {/* Annotation editor */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-semibold text-slate-800">Annotation Editor</h2>
          <p className="text-xs text-slate-400">
            Edit any row, then click <strong>Regenerate PDF</strong> to rebuild the output
          </p>
        </div>

        {loadingDetail && (
          <div className="bg-white border border-slate-200 rounded-xl p-12 flex justify-center">
            <div className="w-8 h-8 border-2 border-blue-200 border-t-blue-600 rounded-full animate-spin" />
          </div>
        )}

        {detailError && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-sm text-red-700 text-center">
            {detailError}
          </div>
        )}

        {detail && !loadingDetail && (
          <AnnotationTable
            resolved={detail.resolved}
            unresolved={detail.unresolved}
            jobId={jobId}
            onRegenerate={handleRegenerate}
            regenerating={regenerating}
          />
        )}
      </div>
    </div>
  );
}
