import { FLAG_MONTH_DATE, FLAG_UNDATED, type NoticeIndex } from "./types";

const HEADER = ["state", "employer", "location", "notice_date", "notice_date_precision", "notice_date_basis", "effective_date", "effective_date_precision", "effective_date_basis", "effective_date_end", "effective_date_end_precision", "effective_date_end_basis", "employees_affected", "layoff_type", "key"];

export function downloadCsv(index: NoticeIndex, rows: number[], filename: string) {
  const { key, state, date, notice_precision, notice_basis, effective, effective_precision,
    effective_basis, effective_end, effective_end_precision, effective_end_basis,
    employer, location, jobs, type, flags } = index.columns;
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
        flags[i] & FLAG_UNDATED ? "" : flags[i] & FLAG_MONTH_DATE ? date[i]?.slice(0, 7) ?? "" : date[i] ?? "",
        notice_precision?.[i] ?? "",
        esc(notice_basis?.[i]),
        effective[i] ?? "",
        effective_precision?.[i] ?? "",
        esc(effective_basis?.[i]),
        effective_end[i] ?? "",
        effective_end_precision?.[i] ?? "",
        esc(effective_end_basis?.[i]),
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
