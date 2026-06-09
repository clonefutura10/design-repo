import axios from "axios";
import type {
  AnnotationResponse,
  AnnotationDetail,
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

export const exportCsv = (jobId: string, rows: import("./types").FieldMapping[]) => {
  const header = ["form_code", "field_label", "annotation", "sdtm_domain", "sdtm_variable", "confidence", "tier", "is_not_submitted"];
  const lines = [
    header.join(","),
    ...rows.map((r) =>
      header.map((h) => {
        const v = String((r as any)[h] ?? "");
        return v.includes(",") || v.includes('"') ? `"${v.replace(/"/g, '""')}"` : v;
      }).join(",")
    ),
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `acrf_mappings_${jobId}.csv`;
  a.click();
  URL.revokeObjectURL(url);
};
