import { Link } from "react-router-dom";
import { useMeta } from "../lib/hooks";
import { num } from "../lib/format";
import { ErrorNote, Skeleton } from "../components/ui/Skeleton";
import { Stamp } from "../components/ui/Stamp";

const STATUS_TONE: Record<string, string> = {
  active: "neutral",
  archive: "unknown",
  broken: "oxide",
  manual_only: "unknown",
  unverified: "unknown",
};
const STATUS_LABEL: Record<string, string> = {
  active: "Automated",
  archive: "Archived data",
  broken: "Source down",
  manual_only: "No public portal",
  unverified: "Unverified",
};

export function StatesIndex() {
  const { data: meta, error } = useMeta();
  if (error) return <ErrorNote message={error} />;
  if (!meta) return <Skeleton lines={8} />;

  const entries = Object.entries(meta.states).sort(([, a], [, b]) =>
    a.name.localeCompare(b.name)
  );
  return (
    <div>
      <h2 className="font-display text-2xl mb-6">State coverage</h2>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-x-10">
        {entries.map(([postal, s]) => (
          <Link
            key={postal}
            to={`/states/${postal.toLowerCase()}`}
            className="flex items-baseline justify-between gap-3 border-b border-rule py-2.5 hover:bg-surface px-1 transition-colors"
          >
            <span className="font-serif">
              {s.name}
              <span className="tabular text-[10px] text-ink-faint ml-2">{postal}</span>
            </span>
            <span className="flex items-center gap-2">
              <span className="tabular text-xs text-ink-muted">{num(s.notices)}</span>
              <Stamp tone={STATUS_TONE[s.status]}>{STATUS_LABEL[s.status] ?? s.status}</Stamp>
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
