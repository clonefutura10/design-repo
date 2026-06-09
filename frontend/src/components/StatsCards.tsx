import React from 'react';
import type { AnnotationStats } from '../types';

interface CardProps {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
}

function Card({ label, value, sub, color = 'text-slate-900' }: CardProps) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 px-4 py-3 flex flex-col gap-0.5 shadow-sm">
      <span className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</span>
      <span className={`text-2xl font-bold ${color}`}>{value}</span>
      {sub && <span className="text-xs text-slate-400">{sub}</span>}
    </div>
  );
}

export default function StatsCards({ stats }: { stats: AnnotationStats }) {
  const rate = stats.resolution_rate;
  const rateColor = rate >= 90 ? 'text-green-600' : rate >= 70 ? 'text-amber-600' : 'text-red-600';

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      <Card
        label="Resolution Rate"
        value={`${rate}%`}
        sub={`${stats.resolved_count} / ${stats.fields_after_noise_filter} fields`}
        color={rateColor}
      />
      <Card
        label="Annotations"
        value={stats.annotations_written}
        sub={`${stats.pages_annotated} pages annotated`}
      />
      <Card
        label="Pages"
        value={stats.total_pages}
        sub={`${stats.unique_forms} unique forms`}
      />
      <Card
        label="NOT SUBMITTED"
        value={stats.not_submitted_count}
        sub="fields excluded"
        color="text-slate-500"
      />
      <Card
        label="Unresolved"
        value={stats.unresolved_count}
        sub="need manual review"
        color={stats.unresolved_count > 0 ? 'text-amber-600' : 'text-slate-900'}
      />
      <div className="bg-white rounded-lg border border-slate-200 px-4 py-3 shadow-sm">
        <span className="text-xs font-medium text-slate-500 uppercase tracking-wide block mb-1">Resolution Tiers</span>
        <div className="space-y-0.5">
          <div className="flex justify-between text-xs">
            <span className="text-slate-600">Rules</span>
            <span className="font-mono font-medium">{stats.tier0_regex}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-slate-600">CDISC Stds</span>
            <span className="font-mono font-medium">{stats.tier0_standards}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-slate-600">Spec</span>
            <span className="font-mono font-medium">{stats.tier0_az_spec}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
