import { FLAG_PUBLIC } from "./types";
import type { NoticeIndex } from "./types";

export interface Filters {
  q: string;
  state: string; // postal or ""
  type: string; // closure|mass_layoff|unknown|""
  from: string; // YYYY-MM-DD or ""
  to: string;
  minJobs: number | null;
  sector: string; // NAICS sector code or ""
  publicOnly: boolean; // only notices matched to an SEC CIK
  sort: SortKey;
  dir: "asc" | "desc";
}

export type SortKey = "date" | "employer" | "jobs" | "state";

export const DEFAULT_FILTERS: Filters = {
  q: "",
  state: "",
  type: "",
  from: "",
  to: "",
  minJobs: null,
  sector: "",
  publicOnly: false,
  sort: "date",
  dir: "desc",
};

export function filtersFromParams(p: URLSearchParams): Filters {
  const dir = p.get("dir");
  const sort = p.get("sort");
  return {
    q: p.get("q") ?? "",
    state: (p.get("state") ?? "").toUpperCase(),
    type: p.get("type") ?? "",
    from: p.get("from") ?? "",
    to: p.get("to") ?? "",
    minJobs: p.get("minJobs") ? Number(p.get("minJobs")) : null,
    sector: p.get("sector") ?? "",
    publicOnly: p.get("public") === "1",
    sort: (["date", "employer", "jobs", "state"] as const).includes(sort as SortKey)
      ? (sort as SortKey)
      : "date",
    dir: dir === "asc" ? "asc" : "desc",
  };
}

export function paramsFromFilters(f: Filters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.state) p.set("state", f.state);
  if (f.type) p.set("type", f.type);
  if (f.from) p.set("from", f.from);
  if (f.to) p.set("to", f.to);
  if (f.minJobs !== null) p.set("minJobs", String(f.minJobs));
  if (f.sector) p.set("sector", f.sector);
  if (f.publicOnly) p.set("public", "1");
  if (f.sort !== "date") p.set("sort", f.sort);
  if (f.dir !== "desc") p.set("dir", f.dir);
  return p;
}

/** Precomputed once per index load: lowercase search haystack per row. */
export function buildHaystack(index: NoticeIndex): string[] {
  const { employer, location } = index.columns;
  const out = new Array<string>(index.count);
  for (let i = 0; i < index.count; i++) {
    out[i] = `${employer[i] ?? ""} ${location[i] ?? ""}`.toLowerCase();
  }
  return out;
}

/** Returns row indices passing the filters, sorted. */
export function applyFilters(
  index: NoticeIndex,
  haystack: string[],
  f: Filters
): number[] {
  const { state, date, jobs, type, flags, sector } = index.columns;
  const stateIdx = f.state ? index.states.indexOf(f.state) : -1;
  const typeIdx = f.type ? index.types.indexOf(f.type) : -1;
  const sectorIdx = f.sector
    ? index.sectors.findIndex((s) => s.code === f.sector)
    : -1;
  const q = f.q.trim().toLowerCase();

  const rows: number[] = [];
  for (let i = 0; i < index.count; i++) {
    if (stateIdx >= 0 && state[i] !== stateIdx) continue;
    if (typeIdx >= 0 && type[i] !== typeIdx) continue;
    const d = date[i];
    if (f.from && (!d || d < f.from)) continue;
    if (f.to && (!d || d > f.to)) continue;
    if (f.minJobs !== null && (jobs[i] ?? -1) < f.minJobs) continue;
    if (sectorIdx >= 0 && sector[i] !== sectorIdx) continue;
    if (f.publicOnly && !(flags[i] & FLAG_PUBLIC)) continue;
    if (q && !haystack[i].includes(q)) continue;
    rows.push(i);
  }
  sortRows(index, rows, f.sort, f.dir);
  return rows;
}

function sortRows(index: NoticeIndex, rows: number[], sort: SortKey, dir: "asc" | "desc") {
  const { date, employer, jobs, state } = index.columns;
  const sign = dir === "asc" ? 1 : -1;
  const cmp: (a: number, b: number) => number =
    sort === "employer"
      ? (a, b) => employer[a].localeCompare(employer[b])
      : sort === "jobs"
        ? (a, b) => (jobs[a] ?? -1) - (jobs[b] ?? -1)
        : sort === "state"
          ? (a, b) => state[a] - state[b] || (date[b] ?? "").localeCompare(date[a] ?? "")
          : (a, b) => (date[a] ?? "").localeCompare(date[b] ?? "");
  rows.sort((a, b) => sign * cmp(a, b) || a - b);
}


export interface FacetCounts {
  states: { value: string; label: string; count: number; weight: number }[];
  sectors: { value: string; label: string; count: number; weight: number }[];
}

/** Facet counts over a result set.
 *
 *  Counted with each facet's own filter lifted, so selecting a sector does
 *  not collapse the sector list to that one value — the counts still answer
 *  "what else is in here", which is the reason to show them at all. */
export function facetCounts(
  index: NoticeIndex,
  haystack: string[],
  f: Filters
): FacetCounts {
  const stateRows = applyFilters(index, haystack, { ...f, state: "" });
  const sectorRows = applyFilters(index, haystack, { ...f, sector: "" });
  const { state, sector, jobs } = index.columns;

  const tally = (rows: number[], col: (i: number) => number, size: number) => {
    const counts = new Array<number>(size).fill(0);
    const weights = new Array<number>(size).fill(0);
    for (const i of rows) {
      const v = col(i);
      if (v < 0) continue;
      counts[v] += 1;
      weights[v] += jobs[i] ?? 0;
    }
    return { counts, weights };
  };

  const st = tally(stateRows, (i) => state[i], index.states.length);
  const sc = tally(sectorRows, (i) => sector[i], index.sectors.length);
  return {
    states: index.states
      .map((code, i) => ({
        value: code, label: code, count: st.counts[i], weight: st.weights[i],
      }))
      .filter((x) => x.count > 0)
      .sort((a, b) => b.count - a.count),
    sectors: index.sectors
      .map((s, i) => ({
        value: s.code, label: s.label, count: sc.counts[i], weight: sc.weights[i],
      }))
      .filter((x) => x.count > 0)
      .sort((a, b) => b.count - a.count),
  };
}
