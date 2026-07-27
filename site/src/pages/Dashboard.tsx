import { lazy, Suspense, useState } from "react";
import { Link } from "react-router-dom";
import { useMeta, useNational } from "../lib/hooks";
import { date, displayName, num } from "../lib/format";
import { StatTile } from "../components/ui/StatTile";
import { SectionHeading } from "../components/ui/SectionHeading";
import { ErrorNote, Skeleton } from "../components/ui/Skeleton";
import { NoticeTable } from "../components/ui/NoticeTable";
import { Bar } from "../components/ui/Bar";
import { MonthlyTrend } from "../components/charts/MonthlyTrend";
import { Choropleth } from "../components/charts/Choropleth";

// The county topology is a megabyte of boundaries, and most visits never open
// the county view — so it is fetched when that view is, not on every load.
const CountyChoropleth = lazy(() =>
  import("../components/charts/CountyChoropleth").then((m) => ({
    default: m.CountyChoropleth,
  }))
);

/** Change against the same window a year earlier; null when there is no
 *  prior period to compare against. */
function change(now: number, before: number): number | null {
  return before > 0 ? (now - before) / before : null;
}

export function Dashboard() {
  const { data: meta, error: metaErr } = useMeta();
  const { data: national, error: natErr } = useNational();
  const [geography, setGeography] = useState<"state" | "county">("state");

  if (metaErr || natErr) return <ErrorNote message={metaErr ?? natErr ?? ""} />;
  if (!meta || !national) return <Skeleton lines={8} />;

  const t12 = national.states_12mo;
  const workers12 = t12.reduce((s, x) => s + x.workers, 0);
  const notices12 = t12.reduce((s, x) => s + x.notices, 0);
  const prior = national.prior_12mo;
  const activeStates = new Set(
    Object.entries(meta.states)
      .filter(([, s]) => s.status === "active")
      .map(([postal]) => postal)
  );
  const mapValues = Object.fromEntries(t12.map((s) => [s.state, s.workers]));
  const countyValues = Object.fromEntries(
    national.counties_12mo.map((c) => [c.fips, c.workers])
  );
  // Say what the county map leaves out rather than letting blank read as none.
  const placedShare = notices12
    ? `${Math.round((national.placed_12mo / notices12) * 100)}%`
    : "none";

  const sectors = national.sectors_12mo.filter((s) => s.sector);
  const sectorPeak = Math.max(1, ...sectors.map((s) => s.workers));
  const priorBySector = new Map(prior.sectors.map((s) => [s.sector, s.workers]));
  const unknownSector = national.sectors_12mo.find((s) => !s.sector);

  return (
    <div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
        <StatTile
          label="Notices, trailing 12 mo"
          value={num(notices12)}
          delta={change(notices12, prior.notices)}
          deltaLabel="vs. prior 12 mo"
          sub={`through ${date(national.anchor_date)}`}
        />
        <StatTile
          label="Workers, trailing 12 mo"
          value={num(workers12)}
          delta={change(workers12, prior.workers)}
          deltaLabel="vs. prior 12 mo"
          sub={`${activeStates.size} states reporting`}
        />
        <StatTile
          label="Notices on record"
          value={num(meta.totals.notices)}
          sub={meta.date_range ? `since ${date(meta.date_range.min)}` : undefined}
        />
        <StatTile
          label="Workers on record"
          value={num(meta.totals.workers)}
          sub="a floor: 5% of notices omit headcount"
        />
      </div>

      <SectionHeading
        sub="Notices are placed by filing date, or by layoff date where a state publishes none."
        right={
          <Link to="/explore" className="smallcaps text-[10px] text-oxide hover:underline">
            Open explorer →
          </Link>
        }
      >
        Notices filed by month
      </SectionHeading>
      <MonthlyTrend monthly={national.monthly} anchor={national.anchor_date} />

      <div className="grid lg:grid-cols-5 gap-10">
        <div className="lg:col-span-3">
          <SectionHeading
            sub={
              geography === "state"
                ? "Trailing 12 months. Unshaded states publish no notice-level list."
                : `Trailing 12 months, across ${num(national.counties_12mo.length)} counties. ` +
                  `${placedShare} of notices in the window name a location a county could be found for.`
            }
            right={
              <div className="flex gap-3">
                {(["state", "county"] as const).map((mode) => (
                  <button
                    key={mode}
                    onClick={() => setGeography(mode)}
                    className={
                      "smallcaps text-[10px] " +
                      (geography === mode
                        ? "text-oxide underline"
                        : "text-ink-muted hover:text-ink")
                    }
                  >
                    {mode === "state" ? "By state" : "By county"}
                  </button>
                ))}
              </div>
            }
          >
            Workers affected by {geography}
          </SectionHeading>
          {geography === "state" ? (
            <Choropleth values={mapValues} activeStates={activeStates}
              label="workers affected, trailing 12 mo" />
          ) : (
            <Suspense fallback={<Skeleton lines={10} />}>
              <CountyChoropleth values={countyValues}
                label="workers affected, trailing 12 mo" />
            </Suspense>
          )}
        </div>
        <div className="lg:col-span-2">
          <SectionHeading
            sub={
              unknownSector
                ? `${num(unknownSector.workers)} workers are in notices with no industry recorded.`
                : undefined
            }
          >
            Industry · trailing 12 months
          </SectionHeading>
          <ul className="space-y-2.5">
            {sectors.slice(0, 10).map((s) => {
              const before = priorBySector.get(s.sector) ?? 0;
              const delta = change(s.workers, before);
              return (
                <li key={s.sector}>
                  <Link
                    to={`/explore?sector=${encodeURIComponent(s.sector!)}`}
                    className="group block"
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[13px] font-serif group-hover:text-oxide truncate">
                        {s.label}
                      </span>
                      <span className="tabular text-xs shrink-0">
                        {num(s.workers)}
                        {delta !== null && Math.abs(delta) >= 0.1 && (
                          <span
                            className={
                              delta > 0 ? "text-oxide ml-1.5" : "text-federal ml-1.5"
                            }
                          >
                            {delta > 0 ? "+" : ""}
                            {Math.round(delta * 100)}%
                          </span>
                        )}
                      </span>
                    </div>
                    <Bar value={s.workers} max={sectorPeak} className="mt-1" />
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      <SectionHeading
        right={
          <Link to="/employers" className="smallcaps text-[10px] text-oxide hover:underline">
            All employers →
          </Link>
        }
        sub="Grouped by company, so filings under different spellings count once."
      >
        Largest employers · trailing 12 months
      </SectionHeading>
      <div className="grid sm:grid-cols-2 gap-x-10">
        {[0, 1].map((col) => (
          <table key={col} className="w-full text-sm">
            <tbody>
              {national.top_employers_12mo
                .slice(col * 8, col * 8 + 8)
                .map((e, i) => (
                  <tr key={e.key} className="border-b border-rule">
                    <td className="tabular text-xs text-ink-faint py-1.5 pr-2 w-6">
                      {col * 8 + i + 1}
                    </td>
                    <td className="py-1.5 pr-3 font-serif">
                      <Link
                        to={`/employers/${encodeURIComponent(e.key)}`}
                        className="hover:underline"
                      >
                        {displayName(e.employer)}
                      </Link>
                    </td>
                    <td className="tabular text-right py-1.5">{num(e.workers)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        ))}
      </div>

      <SectionHeading>Largest notices · trailing 90 days</SectionHeading>
      <NoticeTable notices={national.biggest_recent.slice(0, 15)} />

      <SectionHeading>Coverage</SectionHeading>
      <p className="text-sm font-serif text-ink-muted max-w-3xl leading-relaxed">
        This register consolidates notices from {activeStates.size} state portals;{" "}
        {Object.values(meta.states).filter((s) => s.status === "manual_only").length}{" "}
        states publish nothing online and are absent. History depth varies —
        Illinois reaches back to 1987 while some portals expose only the current
        year — and {Math.round((meta.totals.undated / meta.totals.notices) * 100)}%
        of notices carry no filing date. What each figure here does and does not
        count is set out in{" "}
        <Link to="/methods" className="underline hover:text-ink">methods</Link>.
      </p>
    </div>
  );
}
