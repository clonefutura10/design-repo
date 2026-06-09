import React, { useState, useCallback, useMemo, useRef } from 'react';
import type { FieldMapping, EditRequest } from '../types';
import DomainBadge, { parseAnnotation } from './DomainBadge';

// ── Types ────────────────────────────────────────────────────────────────────

type TabFilter = 'all' | 'resolved' | 'unresolved' | 'modified';

// Key uniquely identifying a field row: "FORMCODE||field label"
function rowKey(row: FieldMapping) {
  return `${row.form_code.toUpperCase()}||${row.field_label.toLowerCase().trim()}`;
}

// All current annotations for a row (primary + additional), respecting edits
function currentAnnotations(row: FieldMapping, edits: Map<string, string[]>): string[] {
  const k = rowKey(row);
  if (edits.has(k)) return edits.get(k)!;
  const base: string[] = [];
  if (row.annotation) base.push(row.annotation);
  if (row.additional_annotations?.length) base.push(...row.additional_annotations);
  return base;
}

function tierLabel(tier: string, confidence: number) {
  if (tier === 'tier1_ns') return { text: 'N/S', cls: 'bg-slate-100 text-slate-500' };
  if (tier === 'unresolved') return { text: '?', cls: 'bg-amber-100 text-amber-700' };
  if (tier === 'user_edit') return { text: 'Edited', cls: 'bg-orange-100 text-orange-700' };
  if (confidence >= 0.96) return { text: 'Learned', cls: 'bg-indigo-100 text-indigo-700' };
  if (confidence >= 0.95) return { text: 'Rule', cls: 'bg-slate-800 text-white' };
  if (confidence >= 0.92) return { text: 'CDISC', cls: 'bg-blue-100 text-blue-700' };
  if (confidence >= 0.86) return { text: 'Spec', cls: 'bg-teal-100 text-teal-700' };
  if (confidence >= 0.72) return { text: 'Fuzzy', cls: 'bg-yellow-100 text-yellow-700' };
  return { text: '?', cls: 'bg-slate-100 text-slate-500' };
}

// ── Annotation input row (one per variable slot in edit mode) ────────────────

interface SlotProps {
  value: string;
  index: number;
  canRemove: boolean;
  onChange: (v: string) => void;
  onRemove: () => void;
}

function AnnotationSlot({ value, index, canRemove, onChange, onRemove }: SlotProps) {
  const parsed = parseAnnotation(value);
  const isValid = value === '' || parsed.isValid;
  const borderCls = value === ''
    ? 'border-slate-300 focus:border-blue-400'
    : parsed.isValid
      ? 'border-green-400 focus:border-green-500'
      : 'border-red-400 focus:border-red-500';

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-slate-400 w-5 text-right flex-shrink-0">{index + 1}.</span>
      <div className="relative flex-1">
        <input
          type="text"
          value={value}
          onChange={e => onChange(e.target.value.toUpperCase())}
          placeholder="e.g. VS.VSORRES or SUPPVS.QVAL"
          className={`w-full border rounded px-2.5 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-300 ${borderCls}`}
          spellCheck={false}
          autoComplete="off"
        />
        {parsed.isValid && value && (
          <div className="absolute right-2 top-1/2 -translate-y-1/2">
            <DomainBadge annotation={value} size="sm" />
          </div>
        )}
      </div>
      {canRemove && (
        <button
          onClick={onRemove}
          className="text-slate-400 hover:text-red-500 transition-colors flex-shrink-0 text-sm leading-none"
          title="Remove this annotation"
        >✕</button>
      )}
    </div>
  );
}

// ── Edit panel (expands below a row) ─────────────────────────────────────────

interface EditPanelProps {
  row: FieldMapping;
  initialSlots: string[];
  onSave: (annotations: string[]) => void;
  onCancel: () => void;
}

function EditPanel({ row, initialSlots, onSave, onCancel }: EditPanelProps) {
  const [slots, setSlots] = useState<string[]>(
    initialSlots.length ? initialSlots : ['']
  );

  const update = (i: number, v: string) =>
    setSlots(s => s.map((x, idx) => idx === i ? v : x));

  const addSlot = () => setSlots(s => [...s, '']);

  const removeSlot = (i: number) =>
    setSlots(s => s.length === 1 ? [''] : s.filter((_, idx) => idx !== i));

  const handleSave = () => {
    const cleaned = slots.map(s => s.trim()).filter(Boolean);
    const valid = cleaned.every(s => parseAnnotation(s).isValid);
    if (cleaned.length && !valid) {
      alert('Some annotations are invalid. Format: DOMAIN.VARIABLE (e.g. VS.VSORRES)');
      return;
    }
    onSave(cleaned);
  };

  const markNS = () => onSave(['NOT SUBMITTED']);
  const deleteAnnotation = () => onSave([]);

  return (
    <tr>
      <td colSpan={6} className="bg-amber-50 border-b border-amber-200 px-4 py-3">
        <div className="flex items-start gap-6">
          {/* Field info */}
          <div className="flex-shrink-0 min-w-0 hidden sm:block">
            <p className="text-xs font-medium text-slate-700 font-mono">{row.form_code}</p>
            <p className="text-xs text-slate-500 mt-0.5 max-w-[160px] truncate" title={row.field_label}>
              {row.field_label}
            </p>
          </div>

          {/* Annotation slots */}
          <div className="flex-1 space-y-2">
            <p className="text-xs font-semibold text-slate-600 mb-1.5">
              SDTM Annotations
              <span className="ml-1.5 font-normal text-slate-400">(first = primary, rest = additional mappings)</span>
            </p>
            {slots.map((s, i) => (
              <AnnotationSlot
                key={i}
                value={s}
                index={i}
                canRemove={slots.length > 1 || s !== ''}
                onChange={v => update(i, v)}
                onRemove={() => removeSlot(i)}
              />
            ))}
            <button
              onClick={addSlot}
              className="text-xs text-blue-600 hover:text-blue-800 font-medium mt-1 flex items-center gap-1"
            >
              <span className="text-base leading-none">+</span> Add annotation
            </button>
          </div>

          {/* Quick actions + save/cancel */}
          <div className="flex flex-col gap-2 flex-shrink-0">
            <div className="flex gap-2">
              <button
                onClick={handleSave}
                className="px-3 py-1.5 bg-blue-700 text-white text-xs font-semibold rounded hover:bg-blue-800 transition-colors"
              >
                Save
              </button>
              <button
                onClick={onCancel}
                className="px-3 py-1.5 bg-white border border-slate-300 text-slate-700 text-xs font-medium rounded hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
            </div>
            <button
              onClick={markNS}
              className="px-3 py-1.5 bg-slate-100 text-slate-600 text-xs rounded hover:bg-slate-200 transition-colors text-left"
            >
              Mark NOT SUBMITTED
            </button>
            <button
              onClick={deleteAnnotation}
              className="px-3 py-1.5 bg-white border border-red-200 text-red-600 text-xs rounded hover:bg-red-50 transition-colors text-left"
            >
              Remove annotation
            </button>
          </div>
        </div>
      </td>
    </tr>
  );
}

// ── Main Table ────────────────────────────────────────────────────────────────

interface Props {
  resolved: FieldMapping[];
  unresolved: FieldMapping[];
  jobId: string;
  onRegenerate: (req: EditRequest) => Promise<void>;
  regenerating: boolean;
}

export default function AnnotationTable({ resolved, unresolved, jobId, onRegenerate, regenerating }: Props) {
  const [tab, setTab] = useState<TabFilter>('all');
  const [search, setSearch] = useState('');
  const [edits, setEdits] = useState<Map<string, string[]>>(new Map());
  const [editingKey, setEditingKey] = useState<string | null>(null);

  // Combine and sort all rows: by form code then field label
  const allRows = useMemo(() => {
    const rows = [
      ...resolved,
      ...unresolved,
    ].sort((a, b) =>
      a.form_code.localeCompare(b.form_code) || a.field_label.localeCompare(b.field_label)
    );
    return rows;
  }, [resolved, unresolved]);

  const filteredRows = useMemo(() => {
    let rows = allRows;

    if (tab === 'resolved') rows = rows.filter(r => r.annotation || r.is_not_submitted);
    else if (tab === 'unresolved') rows = rows.filter(r => !r.annotation && !r.is_not_submitted);
    else if (tab === 'modified') rows = rows.filter(r => edits.has(rowKey(r)));

    if (search.trim()) {
      const q = search.toLowerCase().trim();
      rows = rows.filter(r =>
        r.form_code.toLowerCase().includes(q) ||
        r.field_label.toLowerCase().includes(q) ||
        (r.annotation || '').toLowerCase().includes(q)
      );
    }

    return rows;
  }, [allRows, tab, search, edits]);

  const editCount = edits.size;

  const saveEdit = useCallback((key: string, annotations: string[]) => {
    setEdits(prev => {
      const next = new Map(prev);
      next.set(key, annotations);
      return next;
    });
    setEditingKey(null);
  }, []);

  const cancelEdit = useCallback(() => setEditingKey(null), []);

  const handleRegenerate = async () => {
    if (!editCount) return;
    const overrides = Array.from(edits.entries()).map(([key, annotations]) => {
      const [form_code, ...rest] = key.split('||');
      return { form_code, field_label: rest.join('||'), annotations };
    });
    await onRegenerate({ overrides });
    setEdits(new Map());
  };

  const TABS: { id: TabFilter; label: string }[] = [
    { id: 'all', label: `All (${allRows.length})` },
    { id: 'resolved', label: `Resolved (${resolved.length})` },
    { id: 'unresolved', label: `Unresolved (${unresolved.length})` },
    { id: 'modified', label: `Modified (${editCount})` },
  ];

  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden flex flex-col">
      {/* Toolbar */}
      <div className="px-4 pt-3 pb-0 border-b border-slate-200">
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="relative flex-1 max-w-xs">
            <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 text-xs">🔍</span>
            <input
              type="text"
              placeholder="Search form, label, annotation…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full border border-slate-300 rounded-lg pl-7 pr-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300 focus:border-blue-400"
            />
          </div>
          <p className="text-xs text-slate-400 hidden sm:block">
            Click <strong>Edit</strong> on any row to modify its SDTM annotation
          </p>
        </div>

        {/* Tab strip */}
        <div className="flex gap-1">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors
                ${tab === t.id
                  ? 'border-blue-600 text-blue-700'
                  : 'border-transparent text-slate-500 hover:text-slate-700'}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="overflow-auto table-scroll flex-1">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-3 py-2.5 text-left font-semibold w-24">Form</th>
              <th className="px-3 py-2.5 text-left font-semibold">Field Label</th>
              <th className="px-3 py-2.5 text-left font-semibold">Annotation(s)</th>
              <th className="px-3 py-2.5 text-left font-semibold w-20 hidden md:table-cell">Tier</th>
              <th className="px-3 py-2.5 text-left font-semibold w-20 hidden lg:table-cell">Conf.</th>
              <th className="px-3 py-2.5 text-right font-semibold w-20">Action</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.length === 0 && (
              <tr>
                <td colSpan={6} className="text-center py-12 text-slate-400 text-sm">
                  {search ? 'No fields match the search query.' : 'No fields in this category.'}
                </td>
              </tr>
            )}

            {filteredRows.map(row => {
              const key = rowKey(row);
              const isDirty = edits.has(key);
              const isEditing = editingKey === key;
              const anns = currentAnnotations(row, edits);
              const displayTier = isDirty
                ? { text: 'Edited', cls: 'bg-orange-100 text-orange-700' }
                : tierLabel(row.tier, row.confidence);
              const confPct = row.confidence > 0
                ? `${Math.round(row.confidence * 100)}%`
                : '—';

              return (
                <React.Fragment key={key}>
                  <tr
                    className={`annotation-row border-b border-slate-100 group
                      ${isEditing ? 'bg-amber-50/50' : isDirty ? 'bg-orange-50/40' : 'hover:bg-slate-50/70'}
                    `}
                  >
                    {/* Dirty indicator */}
                    <td className="pl-0 pr-3 py-2.5 relative">
                      <div className={`absolute left-0 top-0 bottom-0 w-1 rounded-r
                        ${isDirty ? 'bg-orange-400' : isEditing ? 'bg-blue-400' : 'bg-transparent'}`}
                      />
                      <span className="ml-2 font-mono text-xs font-semibold text-slate-600 bg-slate-100 rounded px-1.5 py-0.5">
                        {row.form_code}
                      </span>
                    </td>

                    <td className="px-3 py-2.5 max-w-xs">
                      <span className="text-slate-800 text-xs line-clamp-2 leading-4" title={row.field_label}>
                        {row.field_label}
                      </span>
                    </td>

                    <td className="px-3 py-2.5">
                      {anns.length === 0 ? (
                        <span className="text-slate-300 text-xs italic">unresolved</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {anns.map((a, i) => (
                            <DomainBadge key={i} annotation={a} size="sm" />
                          ))}
                        </div>
                      )}
                    </td>

                    <td className="px-3 py-2.5 hidden md:table-cell">
                      <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${displayTier.cls}`}>
                        {displayTier.text}
                      </span>
                    </td>

                    <td className="px-3 py-2.5 hidden lg:table-cell">
                      <span className="text-xs text-slate-500 font-mono">{confPct}</span>
                    </td>

                    <td className="px-3 py-2.5 text-right">
                      {isEditing ? (
                        <button
                          onClick={cancelEdit}
                          className="text-xs text-slate-500 hover:text-slate-700"
                        >
                          Collapse
                        </button>
                      ) : (
                        <button
                          onClick={() => setEditingKey(key)}
                          className="text-xs font-medium text-blue-600 hover:text-blue-800 transition-colors
                            opacity-0 group-hover:opacity-100 focus:opacity-100"
                        >
                          Edit
                        </button>
                      )}
                    </td>
                  </tr>

                  {isEditing && (
                    <EditPanel
                      row={row}
                      initialSlots={anns.length ? anns : ['']}
                      onSave={annotations => saveEdit(key, annotations)}
                      onCancel={cancelEdit}
                    />
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Sticky bottom action bar */}
      {(editCount > 0 || regenerating) && (
        <div className="border-t border-amber-200 bg-amber-50 px-4 py-3 flex items-center justify-between gap-4">
          <div className="text-sm text-amber-800">
            <span className="font-semibold">{editCount} annotation{editCount !== 1 ? 's' : ''} modified</span>
            <span className="ml-2 text-amber-600 hidden sm:inline">— click Regenerate to rebuild the PDF</span>
          </div>
          <div className="flex gap-2 flex-shrink-0">
            <button
              onClick={() => { setEdits(new Map()); setEditingKey(null); }}
              className="px-3 py-1.5 text-xs text-amber-700 hover:text-amber-900 font-medium"
            >
              Discard all
            </button>
            <button
              onClick={handleRegenerate}
              disabled={regenerating}
              className="px-4 py-1.5 bg-blue-700 text-white text-xs font-semibold rounded-lg hover:bg-blue-800 disabled:opacity-60 transition-colors flex items-center gap-2"
            >
              {regenerating && (
                <span className="w-3 h-3 border-2 border-white/40 border-t-white rounded-full animate-spin" />
              )}
              Regenerate PDF
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
