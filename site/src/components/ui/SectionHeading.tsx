import type { ReactNode } from "react";
import clsx from "clsx";

/** A ruled section label. `sub` carries the one line of context a section
 *  usually needs — what the figures below count, or over what window —
 *  which otherwise ends up as an unlabelled paragraph. */
export function SectionHeading({
  children,
  right,
  sub,
  tight,
}: {
  children: ReactNode;
  right?: ReactNode;
  sub?: ReactNode;
  tight?: boolean;
}) {
  return (
    <div className={clsx(tight ? "mt-6 mb-3" : "mt-10 mb-4")}>
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="smallcaps text-xs text-ink-muted">{children}</h2>
        {right}
      </div>
      <div className="border-t border-rule-strong mt-1.5" />
      {sub && (
        <p className="text-xs font-serif text-ink-faint mt-1.5 leading-relaxed">{sub}</p>
      )}
    </div>
  );
}
