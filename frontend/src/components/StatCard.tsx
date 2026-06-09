interface StatCardProps {
  label: string;
  value: string | number;
  sub?: string;
  accent?: boolean;
}

export function StatCard({ label, value, sub, accent = false }: StatCardProps) {
  return (
    <div className="card flex flex-col gap-1">
      <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: "#6B6B6B" }}>{label}</p>
      <p
        className="text-3xl font-bold"
        style={{ color: accent ? "#6B2D88" : "#1A1A1A" }}
      >
        {value}
      </p>
      {sub && <p className="text-xs" style={{ color: "#6B6B6B" }}>{sub}</p>}
    </div>
  );
}
