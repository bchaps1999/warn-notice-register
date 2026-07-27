import clsx from "clsx";

/** A proportion rule: the register's hairline, weighted by magnitude.
 *  Deliberately not a chart — it reads as part of the table it sits in. */
export function Bar({
  value,
  max,
  tone = "ink",
  className,
}: {
  value: number;
  max: number;
  tone?: "ink" | "oxide" | "federal";
  className?: string;
}) {
  const pct = max > 0 ? Math.max(value / max, 0) * 100 : 0;
  return (
    <div className={clsx("h-1 bg-rule/50 w-full", className)}>
      <div
        className={clsx(
          "h-full",
          tone === "oxide" && "bg-oxide",
          tone === "federal" && "bg-federal",
          tone === "ink" && "bg-ink-muted"
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
