import { FLAG_PUBLIC } from "./types";
import type { NoticeIndex } from "./types";

export interface Filters {
  q: string;
  state: string; // postal or ""
  type: string; // closure|mass_layoff|unknown|""
  from: string; // YYYY-MM-DD or ""
  to: string;
  minJobs: number | null;
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
  const { state, date, jobs, type, flags } = index.columns;
  const stateIdx = f.state ? index.states.indexOf(f.state) : -1;
  const typeIdx = f.type ? index.types.indexOf(f.type) : -1;
  const q = f.q.trim().toLowerCase();

  const rows: number[] = [];
  for (let i = 0; i < index.count; i++) {
    if (stateIdx >= 0 && state[i] !== stateIdx) continue;
    if (typeIdx >= 0 && type[i] !== typeIdx) continue;
    const d = date[i];
    if (f.from && (!d || d < f.from)) continue;
    if (f.to && (!d || d > f.to)) continue;
    if (f.minJobs !== null && (jobs[i] ?? -1) < f.minJobs) continue;
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
