import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useIndex } from "../lib/hooks";
import {
  applyFilters,
  buildHaystack,
  facetCounts,
  filtersFromParams,
  paramsFromFilters,
  type Filters,
  type SortKey,
} from "../lib/explorerFilters";
import { downloadCsv } from "../lib/csv";
import { date, num, STATE_NAMES, TYPE_LABEL } from "../lib/format";
import { Stamp } from "../components/ui/Stamp";
import { ErrorNote, Skeleton } from "../components/ui/Skeleton";
import { EmptyState } from "../components/ui/EmptyState";
import { FacetList } from "../components/ui/FacetList";
import { SectionHeading } from "../components/ui/SectionHeading";
import clsx from "clsx";

export function Explorer() {
  const { data: index, error } = useIndex();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => filtersFromParams(params), [params]);
  const setFilters = (patch: Partial<Filters>) =>
    setParams(paramsFromFilters({ ...filters, ...patch }), { replace: true });

  // debounce only the text query
  const [qDraft, setQDraft] = useState(filters.q);
  useEffect(() => setQDraft(filters.q), [filters.q]);
  useEffect(() => {
    const t = setTimeout(() => {
      if (qDraft !== filters.q) setFilters({ q: qDraft });
    }, 150);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qDraft]);

  const haystack = useMemo(() => (index ? buildHaystack(index) : []), [index]);
  const rows = useMemo(
    () => (index ? applyFilters(index, haystack, filters) : []),
    [index, haystack, filters]
  );
  const facets = useMemo(
    () => (index ? facetCounts(index, haystack, filters) : null),
    [index, haystack, filters]
  );
  const shownWorkers = useMemo(
    () => (index ? rows.reduce((sum, i) => sum + (index.columns.jobs[i] ?? 0), 0) : 0),
    [index, rows]
  );

  if (error) return <ErrorNote message={error} />;
  if (!index) return <Skeleton lines={10} />;

  const toggleSort = (key: SortKey) =>
    setFilters(
      filters.sort === key
        ? { dir: filters.dir === "desc" ? "asc" : "desc" }
        : { sort: key, dir: key === "employer" ? "asc" : "desc" }
    );

  return (
    <div>
      {/* filter row */}
      <div className="flex flex-wrap items-end gap-3 mb-4">
        <Field label="Search employer or location" grow>
          <input
            value={qDraft}
            onChange={(e) => setQDraft(e.target.value)}
            placeholder="e.g. Boeing, Chicago…"
            className="input w-full"
          />
        </Field>
        <Field label="State">
          <select
            value={filters.state}
            onChange={(e) => setFilters({ state: e.target.value })}
            className="input"
          >
            <option value="">All</option>
            {index.states.map((s) => (
              <option key={s} value={s}>
                {STATE_NAMES[s] ?? s}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Type">
          <select
            value={filters.type}
            onChange={(e) => setFilters({ type: e.target.value })}
            className="input"
          >
            <option value="">All</option>
            {index.types.map((t) => (
              <option key={t} value={t}>
                {TYPE_LABEL[t] ?? t}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Industry">
          <select
            value={filters.sector}
            onChange={(e) => setFilters({ sector: e.target.value })}
            className="input"
          >
            <option value="">All</option>
            {index.sectors.map((s) => (
              <option key={s.code} value={s.code}>
                {s.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="From">
          <input type="date" value={filters.from}
            onChange={(e) => setFilters({ from: e.target.value })} className="input" />
        </Field>
        <Field label="To">
          <input type="date" value={filters.to}
            onChange={(e) => setFilters({ to: e.target.value })} className="input" />
        </Field>
        <Field label="Min workers">
          <input
            type="number" min={0} value={filters.minJobs ?? ""}
            onChange={(e) =>
              setFilters({ minJobs: e.target.value === "" ? null : Number(e.target.value) })
            }
            className="input w-24"
          />
        </Field>
        <label className="flex items-center gap-1.5 pb-2 text-xs text-ink-muted cursor-pointer select-none">
          <input
            type="checkbox"
            checked={filters.publicOnly}
            onChange={(e) => setFilters({ publicOnly: e.target.checked })}
          />
          Public companies
        </label>
      </div>

      <div className="flex items-center justify-between border-t border-rule-strong pt-2 mb-1">
        <p className="tabular text-xs text-ink-muted">
          {num(rows.length)} of {num(index.count)} notices ·{" "}
          {num(shownWorkers)} workers
        </p>
        <button
          onClick={() => downloadCsv(index, rows, "warn_notices_filtered.csv")}
          className="smallcaps text-[10px] text-oxide hover:underline"
        >
          Download CSV ({num(rows.length)} rows) ↓
        </button>
      </div>

      <div className="grid lg:grid-cols-[1fr_15rem] gap-8 items-start">
        <div className="min-w-0">
          {rows.length > 0 ? (
            <VirtualRows index={index} rows={rows} sort={filters.sort}
              dir={filters.dir} onSort={toggleSort} />
          ) : (
            <EmptyState
              title="No notices match these filters"
              detail="Try widening the date range, lowering the minimum workers, or clearing the industry filter — 35% of notices have no industry recorded and are excluded whenever one is selected."
              action={
                <button
                  type="button"
                  onClick={() => setParams(new URLSearchParams(), { replace: true })}
                  className="smallcaps text-[10px] text-oxide hover:underline"
                >
                  Clear all filters
                </button>
              }
            />
          )}
        </div>

        {facets && (
          <aside className="hidden lg:block">
            <SectionHeading tight>Industry</SectionHeading>
            <FacetList
              facets={facets.sectors}
              selected={filters.sector}
              onSelect={(v) => setFilters({ sector: v })}
              max={10}
              emptyLabel="No industry recorded in these results"
            />
            <SectionHeading tight>States</SectionHeading>
            <FacetList
              facets={facets.states.map((s) => ({
                ...s,
                label: STATE_NAMES[s.value] ?? s.value,
              }))}
              selected={filters.state}
              onSelect={(v) => setFilters({ state: v })}
              max={10}
            />
            <SectionHeading tight>Counties</SectionHeading>
            <FacetList
              facets={facets.counties}
              selected={filters.county}
              onSelect={(v) => setFilters({ county: v })}
              max={10}
              emptyLabel="No notice here names a place a county could be found for"
            />
            <p className="text-[11px] text-ink-faint font-serif mt-4 leading-relaxed">
              Counts are notices in the current results, each facet counted
              with its own filter lifted. Counties come from resolving the
              filed location; notices whose location names no place — a
              workforce area, "statewide" — are in the results but in no
              county.
            </p>
          </aside>
        )}
      </div>
      {/* local styles for inputs */}
      <style>{`
        .input {
          background: var(--color-surface);
          border: 1px solid var(--color-rule);
          padding: 6px 8px;
          font-family: "Inter Variable", system-ui, sans-serif;
          font-size: 13px;
          color: var(--color-ink);
        }
        .input:focus { outline: 1px solid var(--color-oxide); outline-offset: -1px; }
      `}</style>
    </div>
  );
}

const COLS = "grid grid-cols-[3rem_minmax(14rem,2fr)_minmax(8rem,1.2fr)_7rem_7rem_5.5rem_7rem]";

function VirtualRows({
  index,
  rows,
  sort,
  dir,
  onSort,
}: {
  index: NonNullable<ReturnType<typeof useIndex>["data"]>;
  rows: number[];
  sort: SortKey;
  dir: "asc" | "desc";
  onSort: (k: SortKey) => void;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 38,
    overscan: 12,
  });
  const c = index.columns;

  const Header = ({ label, k, right }: { label: string; k?: SortKey; right?: boolean }) => (
    <button
      disabled={!k}
      onClick={k ? () => onSort(k) : undefined}
      className={clsx(
        "smallcaps text-[10px] text-ink-muted py-2 text-left font-semibold",
        right && "text-right",
        k && "hover:text-ink cursor-pointer"
      )}
    >
      {label}
      {k && sort === k && <span className="ml-1">{dir === "desc" ? "▾" : "▴"}</span>}
    </button>
  );

  return (
    <div>
      <div className={clsx(COLS, "gap-3 border-b border-rule-strong")}>
        <Header label="State" k="state" />
        <Header label="Employer" k="employer" />
        <Header label="Location" />
        <Header label="Notice date" k="date" />
        <Header label="Layoff date" k="effective" />
        <Header label="Workers" k="jobs" right />
        <Header label="Type" />
      </div>
      <div
        ref={parentRef}
        className="overflow-y-auto"
        style={{ height: "min(78vh, calc(100vh - 19rem))", minHeight: "24rem" }}
      >
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {virtualizer.getVirtualItems().map((v) => {
            const i = rows[v.index];
            return (
              <Link
                key={v.key}
                to={`/notice/${c.key[i]}`}
                className={clsx(
                  COLS,
                  "gap-3 items-center absolute left-0 right-0 border-b border-rule text-sm hover:bg-surface transition-colors"
                )}
                style={{ top: 0, transform: `translateY(${v.start}px)`, height: v.size }}
              >
                <span className="tabular text-xs text-ink-muted">{index.states[c.state[i]]}</span>
                <span className="font-serif truncate">{c.employer[i]}</span>
                <span className="text-xs text-ink-muted font-serif truncate">
                  {c.location[i] ?? "—"}
                </span>
                <span className="tabular text-xs">{date(c.date[i])}</span>
                <span className="tabular text-xs">{date(c.effective[i])}</span>
                <span className="tabular text-right">{num(c.jobs[i])}</span>
                <span>
                  <Stamp tone={index.types[c.type[i]]}>
                    {TYPE_LABEL[index.types[c.type[i]]]}
                  </Stamp>
                </span>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
  grow,
}: {
  label: string;
  children: React.ReactNode;
  grow?: boolean;
}) {
  return (
    <label className={clsx("block", grow && "flex-1 min-w-56")}>
      <span className="smallcaps text-[10px] text-ink-muted block mb-1">{label}</span>
      {children}
    </label>
  );
}
