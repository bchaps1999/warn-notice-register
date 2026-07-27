import { Link, useParams } from "react-router-dom";
import { useStateData } from "../lib/hooks";
import type { TopEmployer } from "../lib/types";
import { date, num } from "../lib/format";
import { StatTile } from "../components/ui/StatTile";
import { SectionHeading } from "../components/ui/SectionHeading";
import { ErrorNote, Skeleton } from "../components/ui/Skeleton";
import { NoticeTable } from "../components/ui/NoticeTable";
import { MonthlyTrend } from "../components/charts/MonthlyTrend";
import { Stamp } from "../components/ui/Stamp";
import { Bar } from "../components/ui/Bar";
import { NotFound } from "./NotFound";

export function StateProfile() {
  const { xx } = useParams();
  const { data, error } = useStateData(xx);
  if (error) return error.includes("404") ? <NotFound /> : <ErrorNote message={error} />;
  if (!data) return <Skeleton lines={8} />;

  const anchor = data.coverage.latest ?? "2026-01-01";
  const healthy = data.health.latest_verdict === "ok" || data.health.latest_verdict === "degraded";

  return (
    <div>
      <div className="flex items-baseline justify-between flex-wrap gap-3">
        <h2 className="font-display text-3xl">
          {data.name}
          <span className="tabular text-sm text-ink-faint ml-3">{data.state}</span>
        </h2>
        <Stamp tone={data.source.status === "active" ? "neutral" : "oxide"}>
          {data.source.status === "active"
            ? `Automated · ${data.source.cadence ?? "weekly"}`
            : data.source.status === "archive"
              ? "Archived data · source fetch blocked"
              : data.source.status}
        </Stamp>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 mt-8">
        <StatTile label="Notices on record" value={num(data.coverage.notices)} />
        <StatTile label="Records begin" value={data.coverage.earliest ? date(data.coverage.earliest) : "—"} />
        <StatTile label="Most recent notice" value={data.coverage.latest ? date(data.coverage.latest) : "—"} />
        <StatTile
          label="Collection status"
          value={healthy ? "Current" : data.health.latest_verdict ? "Failing" : "—"}
          sub={data.health.last_success ? `last success ${date(data.health.last_success.slice(0, 10))}` : undefined}
        />
      </div>

      {data.monthly.length > 0 && (
        <>
          <SectionHeading>Notices filed by month</SectionHeading>
          <MonthlyTrend monthly={data.monthly} anchor={anchor} />
        </>
      )}

      <div className="grid lg:grid-cols-2 gap-10">
        <div>
          <SectionHeading>Largest employers · all time</SectionHeading>
          <EmployerList rows={data.top_employers} />
        </div>
        <div>
          <SectionHeading>Largest employers · trailing 24 months</SectionHeading>
          <EmployerList rows={data.top_employers_24mo} />
        </div>
      </div>

      {data.counties.length > 0 && (
        <>
          <SectionHeading
            sub={
              `${num(data.coverage.placed)} of ${num(data.coverage.notices)} notices ` +
              `name a location that resolves to a county. The rest are filed ` +
              `against something else — an address that names no town, a ` +
              `workforce area, or nothing at all — and are absent here.`
            }
          >
            Counties
          </SectionHeading>
          <div className="grid sm:grid-cols-2 gap-x-10">
            {data.counties.slice(0, 20).map((c) => (
              <Link
                key={c.fips}
                to={`/explore?state=${data.state}&county=${c.fips}`}
                className="block py-1.5 border-b border-rule/60 hover:bg-surface"
              >
                <div className="flex justify-between items-baseline gap-3 text-sm">
                  <span className="truncate">
                    {c.county.replace(
                      / (County|Parish|Borough|Municipality|Census Area)$/,
                      ""
                    )}
                  </span>
                  <span className="tabular text-ink-muted text-xs shrink-0">
                    {num(c.workers)} · {num(c.notices)}
                  </span>
                </div>
                <Bar value={c.workers} max={data.counties[0].workers} tone="oxide" />
              </Link>
            ))}
          </div>
        </>
      )}

      <SectionHeading>Most recent notices</SectionHeading>
      <NoticeTable notices={data.recent} showState={false} />

      <SectionHeading>Source</SectionHeading>
      <div className="text-sm font-serif text-ink-muted max-w-3xl leading-relaxed space-y-2">
        <p>
          Collected via <strong className="text-ink">{data.source.kind}</strong> adapter
          {data.source.url && (
            <>
              {" from "}
              <a href={data.source.url} className="underline hover:text-ink" target="_blank" rel="noreferrer">
                the state portal
              </a>
            </>
          )}
          . {data.source.notes}
        </p>
        {data.health.latest_error && (
          <p className="text-oxide">Latest collection error: {data.health.latest_error}</p>
        )}
      </div>
    </div>
  );
}

function EmployerList({ rows }: { rows: TopEmployer[] }) {
  if (rows.length === 0) return <p className="text-sm text-ink-faint font-serif">No data.</p>;
  return (
    <table className="w-full text-sm">
      <tbody>
        {rows.slice(0, 10).map((e, i) => (
          <tr key={e.key} className="border-b border-rule">
            <td className="tabular text-xs text-ink-faint py-1.5 pr-2 w-6">{i + 1}</td>
            <td className="py-1.5 pr-3 font-serif">
              <Link to={`/employers/${encodeURIComponent(e.key)}`} className="hover:underline">
                {e.employer}
              </Link>
            </td>
            <td className="tabular text-right py-1.5">{num(e.workers)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
