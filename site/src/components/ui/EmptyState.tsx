import type { ReactNode } from "react";

/** Nothing found. Says what was searched and offers the way back —
 *  an empty result is a state of the data, not an error. */
export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="border-t border-rule py-10 text-center">
      <p className="font-display text-lg">{title}</p>
      {detail && (
        <p className="text-sm font-serif text-ink-muted mt-2 max-w-md mx-auto leading-relaxed">
          {detail}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
