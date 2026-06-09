import { useEffect, useState, useMemo } from "react";
import { useParams, useLocation, Link } from "react-router-dom";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from "recharts";
import {
  Download, ArrowLeft, Search, ChevronDown, ChevronUp,
  Loader2, Filter, Pencil, Check, X, RefreshCw, AlertCircle,
} from "lucide-react";
import { getStats, getDetails, downloadUrl, applyEdits } from "../api/client";
import type { AnnotationResponse, AnnotationDetail, FieldMapping, AnnotationOverride } from "../api/types";
import { StatCard } from "../components/StatCard";
import { DomainBadge } from "../components/Badges";

const PIE_COLORS = ["#6B2D88", "#E5E5E5"];

// Key used to track edits: "FORMCODE::field label"
const rowKey = (r: FieldMapping) => `${r.form_code}::${r.field_label}`;

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const location = useLocation();
  const initData = location.state as AnnotationResponse | null;

  const [job, setJob] = useState<AnnotationResponse | null>(initData);
  const [detail, setDetail] = useState<AnnotationDetail | null>(null);
  const [loading, setLoading] = useState(!initData);
  const [detailLoading, setDetailLoading] = useState(true);

  const [tab, setTab] = useState<"resolved" | "unresolved">("resolved");
  const [search, setSearch] = useState("");
  const [formFilter, setFormFilter] = useState<string>("all");
  const [sortKey, setSortKey] = useState<keyof FieldMapping>("form_code");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  // Edit state
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [pendingEdits, setPendingEdits] = useState<Record<string, string[]>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    if (!id) return;
    if (!initData) getStats(id).then((r) => { setJob(r.data); setLoading(false); });
    getDetails(id).then((r) => { setDetail(r.data); setDetailLoading(false); });
  }, [id]);

  const allRows = useMemo(() => {
    if (!detail) return [];
    return tab === "resolved" ? detail.resolved : detail.unresolved;
  }, [detail, tab]);

  const formCodes = useMemo(
    () => Array.from(new Set(allRows.map((r) => r.form_code))).sort(),
    [allRows],
  );

  const filtered = useMemo(() => {
    return allRows
      .filter((r) => formFilter === "all" || r.form_code === formFilter)
      .filter((r) =>
        r.field_label.toLowerCase().includes(search.toLowerCase()) ||
        r.form_code.toLowerCase().includes(search.toLowerCase()) ||
        (r.annotation ?? "").toLowerCase().includes(search.toLowerCase())
      );
  }, [allRows, search, formFilter]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const av = String(a[sortKey] ?? "");
      const bv = String(b[sortKey] ?? "");
      return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    });
  }, [filtered, sortKey, sortDir]);

  const toggleSort = (key: keyof FieldMapping) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  };

  const startEdit = (row: FieldMapping) => {
    const k = rowKey(row);
    const current = pendingEdits[k] ?? (row.annotation ? [row.annotation] : []);
    setEditValue(current.join(", "));
    setEditingKey(k);
  };

  const confirmEdit = (row: FieldMapping) => {
    const k = rowKey(row);
    const parsed = editValue
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (parsed.length === 0) {
      // Empty = remove annotation
      setPendingEdits((prev) => ({ ...prev, [k]: [] }));
    } else {
      setPendingEdits((prev) => ({ ...prev, [k]: parsed }));
    }
    setEditingKey(null);
  };

  const cancelEdit = () => setEditingKey(null);

  const discardEdits = () => {
    setPendingEdits({});
    setSaveError(null);
    setSaveSuccess(false);
  };

  const handleSave = async () => {
    if (!id || Object.keys(pendingEdits).length === 0) return;
    setSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    const allMappings = [
      ...(detail?.resolved ?? []),
      ...(detail?.unresolved ?? []),
    ];

    const overrides: AnnotationOverride[] = Object.entries(pendingEdits).map(([key, anns]) => {
      const [form_code, field_label] = key.split("::");
      return { form_code, field_label, annotations: anns };
    });

    try {
      const { data } = await applyEdits(id, overrides);
      // Update job stats
      setJob((prev) => prev ? { ...prev, stats: data.stats } : prev);
      // Reload detail
      const detail2 = await getDetails(id);
      setDetail(detail2.data);
      setPendingEdits({});
      setSaveSuccess(true);
    } catch (e: any) {
      setSaveError(e?.response?.data?.detail ?? e.message ?? "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const pendingCount = Object.keys(pendingEdits).length;

  const Th = ({ label, k }: { label: string; k: keyof FieldMapping }) => (
    <th
      onClick={() => toggleSort(k)}
      className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide cursor-pointer select-none whitespace-nowrap"
      style={{ color: "#6B6B6B" }}
    >
      <span className="flex items-center gap-1">
        {label}
        {sortKey === k ? (sortDir === "asc" ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />) : null}
      </span>
    </th>
  );

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <Loader2 className="w-8 h-8 animate-spin" style={{ color: "#6B2D88" }} />
    </div>
  );
  if (!job) return <p style={{ color: "#D32F2F" }}>Job not found.</p>;

  const { stats } = job;
  const pieData = [
    { name: "Resolved", value: stats.resolved_count },
    { name: "Unresolved", value: stats.unresolved_count },
  ];

  return (
    <div className="space-y-6 pb-24">
      {/* Back + header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <Link to="/jobs" className="btn-secondary text-xs mb-3 inline-flex">
            <ArrowLeft className="w-3.5 h-3.5" /> Back to Jobs
          </Link>
          <h1 className="text-2xl font-bold truncate max-w-xl" style={{ color: "#1A1A1A" }}>{job.filename}</h1>
          <p className="text-sm mt-0.5" style={{ color: "#6B6B6B" }}>
            Job ID: <code className="text-xs px-1.5 py-0.5 rounded" style={{ background: "#F7F7F7" }}>{job.job_id}</code>
          </p>
        </div>
        <a href={downloadUrl(job.job_id)} download className="btn-accent">
          <Download className="w-4 h-4" />Download Annotated PDF
        </a>
      </div>

      {/* Message banner */}
      <div className="rounded-card px-4 py-3 text-sm font-medium" style={{ background: "#E6F6EC", border: "1px solid #00843D", color: "#00843D" }}>
        ✓ {job.message}
      </div>

      {/* Save success banner */}
      {saveSuccess && (
        <div className="rounded-card px-4 py-3 text-sm font-medium flex items-center gap-2" style={{ background: "#E0F2FF", border: "1px solid #00699A", color: "#00699A" }}>
          <Check className="w-4 h-4" />
          PDF regenerated with your edits. Download the updated PDF above.
        </div>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Resolution Rate" value={`${stats.resolution_rate}%`} accent sub="Fields mapped to SDTM" />
        <StatCard label="Annotations Written" value={stats.annotations_written} sub={`Across ${stats.pages_annotated} pages`} />
        <StatCard label="Fields Extracted" value={stats.total_fields_extracted} sub={`${stats.noise_removed} noise removed`} />
        <StatCard label="Unique Forms" value={stats.unique_forms} sub="CRF form types" />
      </div>

      {/* Resolution pie chart */}
      <div className="card" style={{ maxWidth: 380 }}>
        <p className="text-sm font-semibold mb-4" style={{ color: "#1A1A1A" }}>Resolution Overview</p>
        <ResponsiveContainer width="100%" height={200}>
          <PieChart>
            <Pie data={pieData} cx="50%" cy="50%" innerRadius={55} outerRadius={85} paddingAngle={3} dataKey="value">
              {pieData.map((_, i) => <Cell key={i} fill={PIE_COLORS[i]} />)}
            </Pie>
            <Tooltip />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      </div>

      {/* Mappings table */}
      <div className="card p-0 overflow-hidden">
        {/* Table toolbar */}
        <div className="px-6 py-4 border-b border-az-border flex flex-wrap items-center gap-4">
          <div>
            <p className="font-semibold" style={{ color: "#1A1A1A" }}>Field Mappings</p>
            <p className="text-xs" style={{ color: "#6B6B6B" }}>
              {detail?.total_mappings ?? "…"} total fields
              {pendingCount > 0 && (
                <span className="ml-2 font-semibold" style={{ color: "#E65100" }}>· {pendingCount} unsaved edit{pendingCount > 1 ? "s" : ""}</span>
              )}
            </p>
          </div>

          {/* Tabs */}
          <div className="flex rounded-btn p-0.5 text-sm" style={{ background: "#F7F7F7" }}>
            {(["resolved", "unresolved"] as const).map((t) => (
              <button
                key={t}
                onClick={() => { setTab(t); setFormFilter("all"); setSearch(""); }}
                className="px-3 py-1.5 rounded-btn font-medium transition-colors"
                style={tab === t
                  ? { background: "#FFFFFF", color: "#6B2D88", boxShadow: "0 1px 3px rgba(0,0,0,0.08)" }
                  : { color: "#6B6B6B" }
                }
              >
                {t === "resolved" ? `Resolved (${detail?.resolved_count ?? "…"})` : `Unresolved (${detail?.unresolved_count ?? "…"})`}
              </button>
            ))}
          </div>

          {/* Search */}
          <div className="flex items-center gap-2 rounded-btn px-3 py-1.5 border" style={{ background: "#F7F7F7", borderColor: "#E5E5E5" }}>
            <Search className="w-3.5 h-3.5" style={{ color: "#6B6B6B" }} />
            <input
              type="text"
              placeholder="Search fields…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="bg-transparent text-sm outline-none w-44"
              style={{ color: "#1A1A1A" }}
            />
          </div>

          {/* Form filter */}
          <div className="flex items-center gap-2 rounded-btn px-3 py-1.5 border" style={{ background: "#F7F7F7", borderColor: "#E5E5E5" }}>
            <Filter className="w-3.5 h-3.5" style={{ color: "#6B6B6B" }} />
            <select
              value={formFilter}
              onChange={(e) => setFormFilter(e.target.value)}
              className="bg-transparent text-sm outline-none cursor-pointer"
              style={{ color: "#1A1A1A" }}
            >
              <option value="all">All Forms</option>
              {formCodes.map((code) => (
                <option key={code} value={code}>{code}</option>
              ))}
            </select>
          </div>
        </div>

        {detailLoading ? (
          <div className="flex justify-center py-16">
            <Loader2 className="w-6 h-6 animate-spin" style={{ color: "#6B2D88" }} />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-az-border" style={{ background: "#F7F7F7" }}>
                <tr>
                  <Th label="Form" k="form_code" />
                  <Th label="Field Label" k="field_label" />
                  <Th label="Annotation" k="annotation" />
                  <Th label="Domain" k="sdtm_domain" />
                  <Th label="Variable" k="sdtm_variable" />
                  <Th label="Confidence" k="confidence" />
                  <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide" style={{ color: "#6B6B6B" }}>Edit</th>
                </tr>
              </thead>
              <tbody>
                {sorted.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-6 py-10 text-center text-sm" style={{ color: "#6B6B6B" }}>
                      No fields match your filters.
                    </td>
                  </tr>
                ) : (
                  sorted.map((row, i) => {
                    const k = rowKey(row);
                    const isEditing = editingKey === k;
                    const isModified = k in pendingEdits;
                    const displayAnnotation = isModified
                      ? pendingEdits[k].join(", ") || "—"
                      : row.annotation || "—";

                    return (
                      <tr
                        key={i}
                        className="transition-colors"
                        style={{
                          borderBottom: "1px solid #E5E5E5",
                          background: isModified ? "#FFFBEB" : i % 2 === 1 ? "#F7F7F7" : "#FFFFFF",
                          borderLeft: isModified ? "3px solid #F5A623" : "3px solid transparent",
                        }}
                        onMouseEnter={(e) => {
                          if (!isModified) e.currentTarget.style.background = "#EDE0F5";
                        }}
                        onMouseLeave={(e) => {
                          if (!isModified) e.currentTarget.style.background = i % 2 === 1 ? "#F7F7F7" : "#FFFFFF";
                        }}
                      >
                        <td className="px-3 py-2.5">
                          <code className="text-xs px-1.5 py-0.5 rounded font-medium" style={{ background: "#EDE0F5", color: "#6B2D88" }}>
                            {row.form_code}
                          </code>
                        </td>
                        <td className="px-3 py-2.5 max-w-xs">
                          <p className="truncate" style={{ color: "#1A1A1A" }}>{row.field_label}</p>
                          {row.is_not_submitted && (
                            <span className="badge text-xs mt-0.5" style={{ background: "#FFF3E0", color: "#E65100" }}>NOT SUBMITTED</span>
                          )}
                        </td>
                        <td className="px-3 py-2.5 min-w-[180px]">
                          {isEditing ? (
                            <div className="flex items-center gap-1">
                              <input
                                autoFocus
                                value={editValue}
                                onChange={(e) => setEditValue(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") confirmEdit(row);
                                  if (e.key === "Escape") cancelEdit();
                                }}
                                placeholder="e.g. VS.VSORRES or NOT SUBMITTED"
                                className="border rounded px-2 py-1 text-xs w-48 outline-none"
                                style={{ borderColor: "#6B2D88", color: "#1A1A1A" }}
                              />
                              <button onClick={() => confirmEdit(row)} className="p-1 rounded hover:bg-green-100" title="Confirm">
                                <Check className="w-3.5 h-3.5" style={{ color: "#00843D" }} />
                              </button>
                              <button onClick={cancelEdit} className="p-1 rounded hover:bg-red-100" title="Cancel">
                                <X className="w-3.5 h-3.5" style={{ color: "#D32F2F" }} />
                              </button>
                            </div>
                          ) : (
                            <code className="text-xs font-mono font-medium" style={{ color: isModified ? "#E65100" : "#00699A" }}>
                              {displayAnnotation}
                            </code>
                          )}
                        </td>
                        <td className="px-3 py-2.5"><DomainBadge domain={row.sdtm_domain} /></td>
                        <td className="px-3 py-2.5 font-mono text-xs" style={{ color: "#1A1A1A" }}>{row.sdtm_variable ?? "—"}</td>
                        <td className="px-3 py-2.5">
                          <div className="flex items-center gap-1.5">
                            <div className="w-16 rounded-full h-1.5" style={{ background: "#E5E5E5" }}>
                              <div className="h-1.5 rounded-full" style={{ width: `${Math.round(row.confidence * 100)}%`, background: "#6B2D88" }} />
                            </div>
                            <span className="text-xs" style={{ color: "#6B6B6B" }}>{Math.round(row.confidence * 100)}%</span>
                          </div>
                        </td>
                        <td className="px-3 py-2.5">
                          {!isEditing && (
                            <button
                              onClick={() => startEdit(row)}
                              className="p-1.5 rounded hover:bg-purple-100 transition-colors"
                              title="Edit annotation"
                            >
                              <Pencil className="w-3.5 h-3.5" style={{ color: "#6B2D88" }} />
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
            <div className="px-6 py-3 border-t border-az-border text-xs" style={{ background: "#F7F7F7", color: "#6B6B6B" }}>
              Showing {sorted.length} of {filtered.length} fields
              {formFilter !== "all" && <span className="ml-2 font-medium" style={{ color: "#6B2D88" }}>· Form: {formFilter}</span>}
            </div>
          </div>
        )}
      </div>

      {/* Sticky edit footer */}
      {pendingCount > 0 && (
        <div
          className="fixed bottom-0 left-64 right-0 px-8 py-4 flex items-center gap-4 border-t shadow-lg z-50"
          style={{ background: "#FFFFFF", borderColor: "#E5E5E5" }}
        >
          <div className="flex items-center gap-2 text-sm font-medium" style={{ color: "#E65100" }}>
            <RefreshCw className="w-4 h-4" />
            {pendingCount} pending edit{pendingCount > 1 ? "s" : ""}
          </div>
          {saveError && (
            <div className="flex items-center gap-1 text-xs" style={{ color: "#D32F2F" }}>
              <AlertCircle className="w-3.5 h-3.5" /> {saveError}
            </div>
          )}
          <div className="ml-auto flex items-center gap-3">
            <button onClick={discardEdits} className="btn-secondary text-xs" disabled={saving}>
              Discard All
            </button>
            <button onClick={handleSave} className="btn-primary text-xs" disabled={saving}>
              {saving ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />Regenerating…</> : <><RefreshCw className="w-3.5 h-3.5" />Save & Regenerate PDF</>}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
