import type { ReactNode } from "react";

export function SectionHeading({
  children,
  right,
}: {
  children: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="mt-10 mb-4">
      <div className="flex items-baseline justify-between">
        <h2 className="smallcaps text-xs text-ink-muted">{children}</h2>
        {right}
      </div>
      <div className="border-t border-rule-strong mt-1.5" />
    </div>
  );
}
