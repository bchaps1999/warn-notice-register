import type { ReactNode } from "react";

/** One labelled fact in a definition list. The label column is fixed so
 *  facts line up down the page whatever their length. */
export function DefRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="smallcaps text-[10px] text-ink-muted pt-0.5">{label}</dt>
      <dd className="font-serif">{children}</dd>
    </>
  );
}

export function DefList({ children }: { children: ReactNode }) {
  return (
    <dl className="grid grid-cols-[11rem_1fr] gap-y-2 text-sm">{children}</dl>
  );
}
