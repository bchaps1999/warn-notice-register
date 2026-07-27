import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useEmployerIndex } from "../lib/hooks";
import { date, displayName, num } from "../lib/format";
import { DataTable, type Column } from "../components/ui/DataTable";
import { EmptyState } from "../components/ui/EmptyState";
import { FacetList } from "../components/ui/FacetList";
import { SectionHeading } from "../components/ui/SectionHeading";
import { ErrorNote, Skeleton } from "../components/ui/Skeleton";

interface Row {
  key: string;
  label: string;
  notices: number;
  workers: number;
  states: string[];
  sector: string | null;
  parent: string | null;
  identified: number;
  first: string | null;
  last: string | null;
}

const PAGE = 100;

export function EmployersIndex() {
  const { data, error } = useEmployerIndex();
  const [params, setParams] = useSearchParams();
  const [shown, setShown] = useState(PAGE);

  const q = params.get("q") ?? "";
  const sector = params.get("sector") ?? "";
  const sort = params.get("sort") ?? "workers";
  const dir = params.get("dir") === "asc" ? "asc" : "desc";
  const patch = (next: Record<string, string>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) v ? p.set(k, v) : p.delete(k);
    setParams(p, { replace: true });
    setShown(PAGE);
  };

  const rows: Row[] = useMemo(() => {
    if (!data) return [];
    const c = data.columns;
    return c.key.map((key, i) => ({
      key,
      label: c.label[i],
      notices: c.notices[i],
      workers: c.workers[i],
      states: c.states[i],
      sector: c.sector[i],
      parent: c.parent[i],
      identified: c.identified[i],
      first: c.first_date[i],
      last: c.last_date[i],
    }));
  }, [data]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const out = rows.filter(
      (r) =>
        (!sector || r.sector === sector) &&
        (!needle ||
          r.label.toLowerCase().includes(needle) ||
          (r.parent ?? "").toLowerCase().includes(needle))
    );
    const key = sort as keyof Row;
    out.sort((a, b) => {
      const av = a[key], bv = b[key];
      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av ?? "").localeCompare(String(bv ?? ""));
      return dir === "asc" ? cmp : -cmp;
    });
    return out;
  }, [rows, q, sector, sort, dir]);

  const sectorFacets = useMemo(() => {
    if (!data) return [];
    const counts = new Map<string, { n: number; w: number }>();
    for (const r of rows) {
      if (!r.sector) continue;
      const e = counts.get(r.sector) ?? { n: 0, w: 0 };
      e.n += 1;
      e.w += r.workers;
      counts.set(r.sector, e);
    }
    return data.sectors
      .filter((s) => counts.has(s.code))
      .map((s) => ({
        value: s.code,
        label: s.label,
        count: counts.get(s.code)!.n,
        weight: counts.get(s.code)!.w,
      }))
      .sort((a, b) => b.weight! - a.weight!);
  }, [data, rows]);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <Skeleton lines={10} />;

  const columns: Column<Row>[] = [
    {
      key: "label",
      header: "Employer",
      sortable: true,
      render: (r) => (
        <>
          <Link
            to={`/employers/${encodeURIComponent(r.key)}`}
            className="hover:underline"
          >
            {displayName(r.label)}
          </Link>
          {r.parent && (
            <span className="text-xs text-ink-faint"> · {displayName(r.parent)}</span>
          )}
        </>
      ),
    },
    {
      key: "states",
      header: "States",
      render: (r) => (
        <span className="tabular text-xs text-ink-muted">
          {r.states.length <= 3 ? r.states.join(" ") : `${r.states.length} states`}
        </span>
      ),
    },
    {
      key: "notices",
      header: "Notices",
      numeric: true,
      sortable: true,
      render: (r) => num(r.notices),
    },
    {
      key: "workers",
      header: "Workers",
      numeric: true,
      sortable: true,
      render: (r) => num(r.workers),
    },
    {
      key: "last",
      header: "Latest",
      numeric: true,
      sortable: true,
      render: (r) => <span className="text-xs">{date(r.last)}</span>,
    },
  ];

  return (
    <div>
      <p className="smallcaps text-[10px] text-ink-muted">Directory</p>
      <h2 className="font-display text-3xl mt-1">Employers</h2>
      <p className="text-sm font-serif text-ink-muted mt-2 max-w-2xl leading-relaxed">
        Every employer that filed more than once, or whose notices reached{" "}
        {num(data.min_workers)} workers — {num(data.listed)} of{" "}
        {num(data.total_employers)} on record. Notices are grouped by company
        rather than by spelling, so a firm that files as “UNITED” in one state
        and “United Airlines, Inc.” in another appears once.
      </p>

      <div className="grid lg:grid-cols-4 gap-8 mt-8">
        <div className="lg:col-span-1">
          <SectionHeading tight>Industry</SectionHeading>
          <FacetList
            facets={sectorFacets}
            selected={sector}
            onSelect={(v) => patch({ sector: v })}
            metric="weight"
            max={20}
          />
          <p className="text-[11px] text-ink-faint font-serif mt-3 leading-relaxed">
            Workers affected. Employers whose industry is unrecorded are absent
            from this list but present in the table.
          </p>
        </div>

        <div className="lg:col-span-3">
          <div className="flex flex-wrap items-end gap-3">
            <label className="grow">
              <span className="smallcaps text-[10px] text-ink-muted block mb-1">
                Search employer or parent
              </span>
              <input
                value={q}
                onChange={(e) => patch({ q: e.target.value })}
                placeholder="Boeing, Textron, hospital…"
                className="w-full bg-surface border border-rule px-2.5 py-1.5 text-sm font-serif focus:outline-none focus:border-oxide"
              />
            </label>
            <p className="tabular text-xs text-ink-faint pb-2">
              {num(filtered.length)} employers
            </p>
          </div>

          <div className="mt-4">
            <DataTable
              columns={columns}
              rows={filtered.slice(0, shown)}
              rowKey={(r) => r.key}
              sort={sort}
              dir={dir}
              onSort={(key) =>
                patch(
                  key === sort
                    ? { dir: dir === "asc" ? "desc" : "asc" }
                    : { sort: key, dir: key === "label" ? "asc" : "desc" }
                )
              }
              empty={
                <EmptyState
                  title="No employers match"
                  detail={
                    <>
                      Nothing here matches {q && <strong>“{q}”</strong>}
                      {q && sector ? " in " : ""}
                      {sector && (
                        <strong>
                          {data.sectors.find((s) => s.code === sector)?.label}
                        </strong>
                      )}
                      . The directory lists employers with more than one notice;
                      a single small filing may still be in the{" "}
                      <Link to="/explore" className="underline">explorer</Link>.
                    </>
                  }
                />
              }
            />
          </div>

          {shown < filtered.length && (
            <button
              type="button"
              onClick={() => setShown((s) => s + PAGE * 4)}
              className="smallcaps text-[10px] text-oxide hover:underline mt-4"
            >
              Show more ({num(filtered.length - shown)} remaining) →
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
