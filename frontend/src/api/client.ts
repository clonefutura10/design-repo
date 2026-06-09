import axios from "axios";
import type {
  AnnotationResponse,
  AnnotationDetail,
  FieldMapping,
  JobSummary,
  AnnotationOverride,
  EditResponse,
} from "./types";

const api = axios.create({ baseURL: "/api/v1" });

export const annotate = (file: File, onProgress?: (pct: number) => void) => {
  const form = new FormData();
  form.append("file", file);
  return api.post<AnnotationResponse>("/annotate", form, {
    onUploadProgress: (e) => {
      if (e.total && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    },
  });
};

export const getStats = (jobId: string) =>
  api.get<AnnotationResponse>(`/annotate/${jobId}/stats`);

export const getDetails = (jobId: string) =>
  api.get<AnnotationDetail>(`/annotate/${jobId}/details`);

export const listJobs = () => api.get<JobSummary[]>("/jobs");

export const applyEdits = (jobId: string, overrides: AnnotationOverride[]) =>
  api.post<EditResponse>(`/annotate/${jobId}/edit`, { overrides });

export const downloadUrl = (jobId: string) =>
  `/api/v1/annotate/${jobId}/download`;

export const exportCsv = (jobId: string, rows: FieldMapping[]): void => {
  const headers = ['Form Code', 'Field Label', 'Annotation', 'Domain', 'Variable', 'Codelist', 'Is Supplemental', 'Is Not Submitted', 'Confidence %', 'Tier'];
  const csvRows = rows.map(r => [
    r.form_code,
    `"${r.field_label.replace(/"/g, '""')}"`,
    r.annotation || '',
    r.sdtm_domain || '',
    r.sdtm_variable || '',
    r.codelist_code || '',
    r.is_supplemental ? 'Yes' : 'No',
    r.is_not_submitted ? 'Yes' : 'No',
    Math.round(r.confidence * 100).toString(),
    r.tier,
  ].join(','));
  const csv = [headers.join(','), ...csvRows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `aCRF_mappings_${jobId}.csv`;
  a.click();
  URL.revokeObjectURL(url);
};
