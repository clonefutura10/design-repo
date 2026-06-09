// Domain colour map derived from AZ domain_colours config
const DOMAIN_COLOURS: Record<string, { bg: string; color: string }> = {
  DM: { bg: "#EDE0F5", color: "#6B2D88" },
  VS: { bg: "#E0F2FF", color: "#00699A" },
  LB: { bg: "#E0F2FF", color: "#00699A" },
  AE: { bg: "#FDEAEA", color: "#D32F2F" },
  CM: { bg: "#E6F6EC", color: "#00843D" },
  EG: { bg: "#E0F2FF", color: "#00699A" },
  MH: { bg: "#FDEAEA", color: "#D32F2F" },
  DS: { bg: "#EDE0F5", color: "#6B2D88" },
  EX: { bg: "#E6F6EC", color: "#00843D" },
  PE: { bg: "#FFF3E0", color: "#E65100" },
};

export function DomainBadge({ domain }: { domain: string | null }) {
  if (!domain) return <span className="badge" style={{ background: "#F7F7F7", color: "#6B6B6B" }}>—</span>;
  const s = DOMAIN_COLOURS[domain] ?? { bg: "#F7F7F7", color: "#1A1A1A" };
  return (
    <span className="badge font-semibold" style={{ background: s.bg, color: s.color }}>
      {domain}
    </span>
  );
}
