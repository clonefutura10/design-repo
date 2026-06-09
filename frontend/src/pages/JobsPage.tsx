import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Loader2, ExternalLink, FileText, UploadCloud } from "lucide-react";
import { listJobs } from "../api/client";
import type { JobSummary } from "../api/types";

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listJobs()
      .then((r) => setJobs(r.data))
      .catch((e) => setError(e?.response?.data?.detail ?? "Failed to load jobs"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: "#1A1A1A" }}>Job History</h1>
          <p className="text-sm mt-0.5" style={{ color: "#6B6B6B" }}>All completed annotation jobs</p>
        </div>
        <Link to="/" className="btn-primary">
          <UploadCloud className="w-4 h-4" />New Annotation
        </Link>
      </div>

      {loading && (
        <div className="flex justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin" style={{ color: "#6B2D88" }} />
        </div>
      )}

      {error && <p className="text-sm" style={{ color: "#D32F2F" }}>{error}</p>}

      {!loading && jobs.length === 0 && (
        <div className="card flex flex-col items-center py-16 gap-4 text-center">
          <FileText className="w-12 h-12" style={{ color: "#E5E5E5" }} />
          <p className="font-semibold" style={{ color: "#6B6B6B" }}>No jobs yet</p>
          <p className="text-sm" style={{ color: "#6B6B6B" }}>Upload a CRF PDF to run your first annotation.</p>
          <Link to="/" className="btn-primary mt-2">
            <UploadCloud className="w-4 h-4" />Upload CRF
          </Link>
        </div>
      )}

      {jobs.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-az-border" style={{ background: "#F7F7F7" }}>
              <tr>
                {["Filename", "Job ID", "Annotations", "Resolution Rate", "Status", ""].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide" style={{ color: "#6B6B6B" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {jobs.map((job, i) => (
                <tr
                  key={job.job_id}
                  style={{ borderBottom: "1px solid #E5E5E5", background: i % 2 === 1 ? "#F7F7F7" : "#FFFFFF" }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "#EDE0F5")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = i % 2 === 1 ? "#F7F7F7" : "#FFFFFF")}
                >
                  <td className="px-4 py-3 font-medium max-w-xs">
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 shrink-0" style={{ color: "#6B2D88" }} />
                      <span className="truncate" style={{ color: "#1A1A1A" }}>{job.filename}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <code className="text-xs px-1.5 py-0.5 rounded" style={{ background: "#EDE0F5", color: "#6B2D88" }}>
                      {job.job_id.slice(0, 8)}…
                    </code>
                  </td>
                  <td className="px-4 py-3" style={{ color: "#1A1A1A" }}>{job.annotations_written}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-24 rounded-full h-1.5" style={{ background: "#E5E5E5" }}>
                        <div className="h-1.5 rounded-full" style={{ width: `${job.resolution_rate}%`, background: "#6B2D88" }} />
                      </div>
                      <span className="text-xs w-10" style={{ color: "#6B6B6B" }}>{job.resolution_rate}%</span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="badge font-medium" style={{ background: "#E6F6EC", color: "#00843D" }}>{job.status}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link to={`/jobs/${job.job_id}`} className="btn-secondary text-xs">
                      <ExternalLink className="w-3.5 h-3.5" />View
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
