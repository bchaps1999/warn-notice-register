import type { ReactNode } from "react";

/** A caveat set apart from the prose. Used where a figure would mislead
 *  without it — a state whose history starts late, a field its portal
 *  never published. */
export function Callout({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="border-l-2 border-oxide pl-4 py-1 my-4">
      {title && <p className="smallcaps text-[10px] text-oxide mb-1">{title}</p>}
      <div className="text-sm font-serif text-ink-muted leading-relaxed">{children}</div>
    </div>
  );
}
