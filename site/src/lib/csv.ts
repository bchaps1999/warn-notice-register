import { FLAG_UNDATED, type NoticeIndex } from "./types";

const HEADER = ["state", "employer", "location", "notice_date", "effective_date", "employees_affected", "layoff_type", "key"];

export function downloadCsv(index: NoticeIndex, rows: number[], filename: string) {
  const { key, state, date, effective, employer, location, jobs, type, flags } = index.columns;
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [HEADER.join(",")];
  for (const i of rows) {
    lines.push(
      [
        index.states[state[i]],
        esc(employer[i]),
        esc(location[i]),
        flags[i] & FLAG_UNDATED ? "" : date[i] ?? "",
        effective[i] ?? "",
        jobs[i] ?? "",
        index.types[type[i]],
        key[i],
      ].join(",")
    );
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
