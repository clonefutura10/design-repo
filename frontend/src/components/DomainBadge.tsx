import React from 'react';
import type { ParsedAnnotation } from '../types';

// ── Color map — base domain → Tailwind classes ──────────────────────────────
const DOMAIN_COLORS: Record<string, string> = {
  // Events
  AE: 'bg-red-100 text-red-800 border-red-300',
  CE: 'bg-red-100 text-red-800 border-red-300',
  DD: 'bg-red-100 text-red-800 border-red-300',
  HO: 'bg-red-100 text-red-800 border-red-300',
  MH: 'bg-red-100 text-red-800 border-red-300',
  // Interventions
  CM: 'bg-green-100 text-green-800 border-green-300',
  EC: 'bg-green-100 text-green-800 border-green-300',
  EX: 'bg-green-100 text-green-800 border-green-300',
  PR: 'bg-green-100 text-green-800 border-green-300',
  SU: 'bg-green-100 text-green-800 border-green-300',
  // Findings
  BE: 'bg-blue-100 text-blue-800 border-blue-300',
  EG: 'bg-blue-100 text-blue-800 border-blue-300',
  FA: 'bg-blue-100 text-blue-800 border-blue-300',
  IS: 'bg-blue-100 text-blue-800 border-blue-300',
  LB: 'bg-blue-100 text-blue-800 border-blue-300',
  MB: 'bg-blue-100 text-blue-800 border-blue-300',
  PC: 'bg-cyan-100 text-cyan-800 border-cyan-300',
  PE: 'bg-blue-100 text-blue-800 border-blue-300',
  QS: 'bg-blue-100 text-blue-800 border-blue-300',
  RE: 'bg-blue-100 text-blue-800 border-blue-300',
  RP: 'bg-blue-100 text-blue-800 border-blue-300',
  VS: 'bg-blue-100 text-blue-800 border-blue-300',
  // Special
  CO: 'bg-purple-100 text-purple-800 border-purple-300',
  DM: 'bg-purple-100 text-purple-800 border-purple-300',
  DS: 'bg-purple-100 text-purple-800 border-purple-300',
  IE: 'bg-purple-100 text-purple-800 border-purple-300',
  SC: 'bg-purple-100 text-purple-800 border-purple-300',
  SV: 'bg-purple-100 text-purple-800 border-purple-300',
  TI: 'bg-purple-100 text-purple-800 border-purple-300',
  // Oncology
  RS: 'bg-rose-100 text-rose-900 border-rose-400',
  TR: 'bg-rose-100 text-rose-900 border-rose-400',
  TU: 'bg-rose-100 text-rose-900 border-rose-400',
};

export function getDomainColors(domain: string): string {
  return DOMAIN_COLORS[domain.toUpperCase()] ?? 'bg-slate-100 text-slate-700 border-slate-300';
}

/** Parse "VS.VSORRES", "SUPPVS.QVAL (C66770)", "NOT SUBMITTED", "" */
export function parseAnnotation(raw: string): ParsedAnnotation {
  const s = raw.trim();

  if (!s) return { raw, domain: '', variable: '', isSupp: false, codelist: '', isNotSubmitted: false, isValid: false };

  if (s.toUpperCase() === 'NOT SUBMITTED') {
    return { raw, domain: '', variable: '', isSupp: false, codelist: '', isNotSubmitted: true, isValid: true };
  }

  // "SUPPVS.QVAL (C66770)" or "VS.VSORRES"
  const m = s.toUpperCase().match(/^(SUPP)?([A-Z]{2,6})\.([A-Z0-9]+)(?:\s*\(([^)]+)\))?$/);
  if (!m) return { raw, domain: '', variable: '', isSupp: false, codelist: '', isNotSubmitted: false, isValid: false };

  return {
    raw,
    domain: m[2],
    variable: m[3],
    isSupp: !!m[1],
    codelist: m[4] ?? '',
    isNotSubmitted: false,
    isValid: true,
  };
}

interface Props {
  annotation: string;
  size?: 'sm' | 'md';
}

export default function DomainBadge({ annotation, size = 'sm' }: Props) {
  const p = parseAnnotation(annotation);

  if (!p.isValid) return null;

  if (p.isNotSubmitted) {
    return (
      <span className={`inline-flex items-center border rounded font-mono font-medium
        bg-slate-100 text-slate-500 border-slate-300
        ${size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-1 text-xs'}`}>
        NOT SUBMITTED
      </span>
    );
  }

  const label = `${p.isSupp ? 'SUPP' : ''}${p.domain}.${p.variable}`;
  const colors = getDomainColors(p.domain);

  return (
    <span className={`inline-flex items-center border rounded font-mono font-medium
      ${colors}
      ${size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-1 text-xs'}`}>
      {label}
      {p.codelist && <span className="ml-1 opacity-60">({p.codelist})</span>}
    </span>
  );
}
