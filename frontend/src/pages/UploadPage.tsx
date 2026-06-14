import { useState, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { useNavigate } from "react-router-dom";
import {
  UploadCloud, FileText, Loader2, AlertCircle,
  Database, Cpu, FileSearch, Layers, PenTool,
} from "lucide-react";
import { clsx } from "clsx";
import { annotate } from "../api/client";

const PIPELINE_STEPS = [
  {
    icon: Database,
    title: "Document & Standards Intake",
    color: "#6B2D88",
    bg: "#EDE0F5",
    desc: "Accepts blank CRF PDFs alongside SDTM standards, controlled terminology, and study-specific mappings to ground every decision in authoritative sources.",
  },
  {
    icon: Cpu,
    title: "Smart Knowledge Index",
    color: "#00699A",
    bg: "#E0F2FF",
    desc: "Builds a high-speed, structured knowledge base from your standards and specs so field lookups happen instantly at any scale.",
  },
  {
    icon: FileSearch,
    title: "Form Structure Recognition",
    color: "#00843D",
    bg: "#E6F6EC",
    desc: "Reads each page to identify CRF forms, visit folders, field labels, and data-entry patterns — filtering noise so only meaningful fields are passed forward.",
  },
  {
    icon: Layers,
    title: "Intelligent SDTM Mapping",
    color: "#E65100",
    bg: "#FFF3E0",
    desc: "Maps every extracted field to the correct SDTM domain, variable, codelist, and qualifier with high-confidence scoring and supplemental dataset support.",
  },
  {
    icon: PenTool,
    title: "Annotated CRF Generation",
    color: "#D32F2F",
    bg: "#FDEAEA",
    desc: "Produces a standards-compliant aCRF with colour-coded domain headers, extractable FreeText annotations, dataset bookmarks, and cross-page references.",
  },
];

export default function UploadPage() {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onDrop = useCallback((accepted: File[]) => {
    setError(null);
    if (accepted[0]) setFile(accepted[0]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"] },
    maxFiles: 1,
    maxSize: 150 * 1024 * 1024,
  });

  const handleSubmit = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const { data } = await annotate(file, setProgress);
      navigate(`/jobs/${data.job_id}`, { state: data });
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e.message ?? "Unexpected error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "#1A1A1A" }}>Annotate CRF</h1>
        <p className="mt-1 text-sm" style={{ color: "#6B6B6B" }}>
          Upload a blank CRF PDF and the engine will produce a fully annotated aCRF with SDTM variable mappings.
        </p>
      </div>

      {/* Drop zone */}
      <div
        {...getRootProps()}
        className={clsx(
          "border-2 border-dashed rounded-card p-10 text-center cursor-pointer transition-colors",
          isDragActive
            ? "border-az-primary bg-purple-50"
            : file
            ? "border-az-success bg-green-50"
            : "border-az-border hover:border-az-primary hover:bg-az-surface"
        )}
      >
        <input {...getInputProps()} />
        {file ? (
          <div className="flex flex-col items-center gap-3">
            <FileText className="w-12 h-12" style={{ color: "#00843D" }} />
            <p className="font-semibold" style={{ color: "#1A1A1A" }}>{file.name}</p>
            <p className="text-sm" style={{ color: "#6B6B6B" }}>{(file.size / 1024 / 1024).toFixed(2)} MB</p>
            <p className="text-xs" style={{ color: "#6B6B6B" }}>Drop a new file to replace</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3">
            <UploadCloud className="w-12 h-12" style={{ color: "#E5E5E5" }} />
            <p className="font-semibold" style={{ color: "#1A1A1A" }}>
              {isDragActive ? "Drop your PDF here" : "Drag & drop your blank CRF PDF"}
            </p>
            <p className="text-sm" style={{ color: "#6B6B6B" }}>or click to browse · PDF only · max 150 MB</p>
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-3 rounded-card p-4 text-sm" style={{ background: "#FDEAEA", border: "1px solid #D32F2F", color: "#D32F2F" }}>
          <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {/* Progress */}
      {loading && (
        <div>
          <div className="flex justify-between text-xs mb-1" style={{ color: "#6B6B6B" }}>
            <span>Uploading & processing…</span>
            <span>{progress}%</span>
          </div>
          <div className="w-full rounded-full h-2" style={{ background: "#E5E5E5" }}>
            <div
              className="h-2 rounded-full transition-all duration-300"
              style={{ width: `${progress}%`, background: "#6B2D88" }}
            />
          </div>
          <p className="text-xs mt-2 text-center" style={{ color: "#6B6B6B" }}>
            Annotation may take 30–90 seconds for large PDFs…
          </p>
        </div>
      )}

      {/* Submit */}
      <button
        onClick={handleSubmit}
        disabled={!file || loading}
        className="btn-primary w-full justify-center py-3 text-base"
      >
        {loading ? (
          <><Loader2 className="w-4 h-4 animate-spin" />Processing…</>
        ) : (
          <><UploadCloud className="w-4 h-4" />Generate Annotated CRF</>
        )}
      </button>

      {/* Pipeline Overview */}
      <div>
        <h2 className="text-base font-semibold mb-4" style={{ color: "#1A1A1A" }}>
          How It Works
        </h2>
        <div className="space-y-3">
          {PIPELINE_STEPS.map(({ icon: Icon, title, color, bg, desc }, i) => (
            <div
              key={i}
              className="rounded-card shadow-az border border-az-border p-4 flex gap-4"
              style={{ background: "#FFFFFF" }}
            >
              <div
                className="shrink-0 w-9 h-9 rounded-btn flex items-center justify-center"
                style={{ background: bg }}
              >
                <Icon className="w-4 h-4" style={{ color }} />
              </div>
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className="text-xs font-bold rounded-full px-2 py-0.5"
                    style={{ background: bg, color }}
                  >
                    Step {i + 1}
                  </span>
                  <p className="text-sm font-semibold" style={{ color: "#1A1A1A" }}>{title}</p>
                </div>
                <p className="text-sm leading-relaxed" style={{ color: "#6B6B6B" }}>{desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
