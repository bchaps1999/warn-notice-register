import { Link } from "react-router-dom";
import type { NoticeSummary } from "../../lib/types";
import { date, num, TYPE_LABEL } from "../../lib/format";
import { Stamp } from "./Stamp";

export function NoticeTable({
  notices,
  showState = true,
}: {
  notices: NoticeSummary[];
  showState?: boolean;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left border-b border-rule-strong">
            {showState && <Th>State</Th>}
            <Th>Employer</Th>
            <Th>Location</Th>
            <Th>Notice date</Th>
            <Th className="text-right">Workers</Th>
            <Th>Type</Th>
          </tr>
        </thead>
        <tbody>
          {notices.map((n) => (
            <tr key={n.key} className="border-b border-rule hover:bg-surface transition-colors">
              {showState && (
                <td className="py-2 pr-3 tabular text-xs text-ink-muted">{n.state}</td>
              )}
              <td className="py-2 pr-3 font-serif">
                <Link to={`/notice/${n.key}`} className="hover:underline">
                  {n.employer}
                </Link>
              </td>
              <td className="py-2 pr-3 text-ink-muted text-xs font-serif">{n.location ?? "—"}</td>
              <td className="py-2 pr-3 tabular text-xs whitespace-nowrap">{date(n.notice_date)}</td>
              <td className="py-2 pr-3 tabular text-right">{num(n.jobs)}</td>
              <td className="py-2">
                <Stamp tone={n.type}>{TYPE_LABEL[n.type] ?? n.type}</Stamp>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <th className={`smallcaps text-[10px] text-ink-muted py-2 pr-3 font-semibold ${className}`}>{children}</th>;
}
