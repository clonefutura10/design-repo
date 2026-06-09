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
