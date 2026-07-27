import clsx from "clsx";

/** A headline figure. `delta` states the change against a comparable
 *  period — a level alone cannot say whether it is unusual, which is the
 *  question people actually bring to layoff data. */
export function StatTile({
  label,
  value,
  sub,
  delta,
  deltaLabel,
}: {
  label: string;
  value: string;
  sub?: string;
  delta?: number | null;
  deltaLabel?: string;
}) {
  const showDelta = delta !== undefined && delta !== null && Number.isFinite(delta);
  return (
    <div className="border-t-2 border-rule-strong pt-2">
      <p className="smallcaps text-[10px] text-ink-muted">{label}</p>
      <p className="tabular text-3xl font-semibold mt-1 leading-none">{value}</p>
      {showDelta && (
        <p className="mt-1.5 text-xs font-serif">
          <span
            className={clsx(
              "tabular font-semibold",
              delta! > 0 ? "text-oxide" : delta! < 0 ? "text-federal" : "text-ink-muted"
            )}
          >
            {delta! > 0 ? "+" : ""}
            {Math.round(delta! * 100)}%
          </span>
          {deltaLabel && <span className="text-ink-faint ml-1.5">{deltaLabel}</span>}
        </p>
      )}
      {sub && <p className="text-xs text-ink-faint mt-0.5 font-serif">{sub}</p>}
    </div>
  );
}
