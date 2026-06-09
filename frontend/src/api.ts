import type {
  AnnotationResponse,
  AnnotationDetail,
  EditRequest,
  EditResponse,
} from './types';

const BASE = '/api/v1';

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function uploadCRF(file: File): Promise<AnnotationResponse> {
  const form = new FormData();
  form.append('file', file);
  return handle<AnnotationResponse>(
    await fetch(`${BASE}/annotate`, { method: 'POST', body: form }),
  );
}

export async function getDetails(jobId: string): Promise<AnnotationDetail> {
  return handle<AnnotationDetail>(await fetch(`${BASE}/annotate/${jobId}/details`));
}

export async function applyEdits(jobId: string, req: EditRequest): Promise<EditResponse> {
  return handle<EditResponse>(
    await fetch(`${BASE}/annotate/${jobId}/edit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    }),
  );
}

export function downloadUrl(jobId: string): string {
  return `${BASE}/annotate/${jobId}/download`;
}
