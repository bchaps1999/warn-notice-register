import clsx from "clsx";
import { num } from "../../lib/format";
import { Bar } from "./Bar";

export interface Facet {
  value: string;
  label: string;
  count: number;
  weight?: number; // workers, when the count is notices
}

/** Facet values with their counts, as a filter control.
 *
 *  Counts are computed over the *current* result set, so the list doubles
 *  as a breakdown of what you are looking at: selecting a sector and then
 *  reading the state counts answers a question the table cannot. */
export function FacetList({
  facets,
  selected,
  onSelect,
  metric = "count",
  max = 8,
  emptyLabel = "No values",
}: {
  facets: Facet[];
  selected: string;
  onSelect: (value: string) => void;
  metric?: "count" | "weight";
  max?: number;
  emptyLabel?: string;
}) {
  const shown = facets.slice(0, max);
  const peak = Math.max(
    1,
    ...shown.map((f) => (metric === "weight" ? f.weight ?? 0 : f.count))
  );
  if (!shown.length) {
    return <p className="text-xs text-ink-faint font-serif py-1">{emptyLabel}</p>;
  }
  return (
    <ul className="space-y-1.5">
      {shown.map((f) => {
        const active = selected === f.value;
        const value = metric === "weight" ? f.weight ?? 0 : f.count;
        return (
          <li key={f.value}>
            <button
              type="button"
              onClick={() => onSelect(active ? "" : f.value)}
              aria-pressed={active}
              className="w-full text-left group"
            >
              <div className="flex items-baseline justify-between gap-2">
                <span
                  className={clsx(
                    "text-[13px] font-serif truncate",
                    active ? "text-oxide font-semibold" : "text-ink group-hover:text-oxide"
                  )}
                >
                  {f.label}
                </span>
                <span className="tabular text-[11px] text-ink-faint shrink-0">
                  {num(value)}
                </span>
              </div>
              <Bar
                value={value}
                max={peak}
                tone={active ? "oxide" : "ink"}
                className="mt-1"
              />
            </button>
          </li>
        );
      })}
    </ul>
  );
}
