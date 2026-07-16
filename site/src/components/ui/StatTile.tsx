export function StatTile({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="border-t-2 border-rule-strong pt-2">
      <p className="smallcaps text-[10px] text-ink-muted">{label}</p>
      <p className="tabular text-3xl font-semibold mt-1">{value}</p>
      {sub && <p className="text-xs text-ink-faint mt-0.5 font-serif">{sub}</p>}
    </div>
  );
}
