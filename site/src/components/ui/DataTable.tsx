import type { ReactNode } from "react";
import clsx from "clsx";

export interface Column<T> {
  key: string;
  header: string;
  /** Right-aligned tabular figures: the default for quantities. */
  numeric?: boolean;
  sortable?: boolean;
  width?: string;
  render: (row: T) => ReactNode;
}

/** The register's table: ruled rows, small-caps headers, no zebra striping.
 *  Sorting is controlled by the caller so it can live in the URL. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  sort,
  dir,
  onSort,
  empty,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  sort?: string;
  dir?: "asc" | "desc";
  onSort?: (key: string) => void;
  empty?: ReactNode;
}) {
  if (!rows.length && empty) return <>{empty}</>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-rule-strong">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                style={c.width ? { width: c.width } : undefined}
                className={clsx(
                  "smallcaps text-[10px] text-ink-muted font-semibold py-2",
                  c.numeric ? "text-right pl-3" : "text-left pr-3"
                )}
              >
                {c.sortable && onSort ? (
                  <button
                    type="button"
                    onClick={() => onSort(c.key)}
                    className="smallcaps hover:text-ink"
                    aria-sort={sort === c.key ? (dir === "asc" ? "ascending" : "descending") : "none"}
                  >
                    {c.header}
                    {sort === c.key && (
                      <span aria-hidden className="ml-1 text-oxide">
                        {dir === "asc" ? "▲" : "▼"}
                      </span>
                    )}
                  </button>
                ) : (
                  c.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-b border-rule align-baseline">
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={clsx(
                    "py-2",
                    c.numeric ? "tabular text-right pl-3" : "pr-3 font-serif"
                  )}
                >
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
