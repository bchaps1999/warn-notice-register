import { Link } from "react-router-dom";
import { useMeta, useNational } from "../lib/hooks";
import { date, num } from "../lib/format";
import { StatTile } from "../components/ui/StatTile";
import { SectionHeading } from "../components/ui/SectionHeading";
import { ErrorNote, Skeleton } from "../components/ui/Skeleton";
import { NoticeTable } from "../components/ui/NoticeTable";
import { MonthlyTrend } from "../components/charts/MonthlyTrend";
import { Choropleth } from "../components/charts/Choropleth";

export function Dashboard() {
  const { data: meta, error: metaErr } = useMeta();
  const { data: national, error: natErr } = useNational();

  if (metaErr || natErr) return <ErrorNote message={metaErr ?? natErr ?? ""} />;
  if (!meta || !national) return <Skeleton lines={8} />;

  const t12 = national.states_12mo;
  const workers12 = t12.reduce((s, x) => s + x.workers, 0);
  const notices12 = t12.reduce((s, x) => s + x.notices, 0);
  const activeStates = new Set(
    Object.entries(meta.states)
      .filter(([, s]) => s.status === "active")
      .map(([postal]) => postal)
  );
  const mapValues = Object.fromEntries(t12.map((s) => [s.state, s.workers]));

  return (
    <div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
        <StatTile label="Notices on record" value={num(meta.totals.notices)}
          sub={meta.date_range ? `since ${date(meta.date_range.min)}` : undefined} />
        <StatTile label="Workers affected" value={num(meta.totals.workers)} sub="all recorded notices" />
        <StatTile label="Notices, trailing 12 mo" value={num(notices12)}
          sub={`through ${date(national.anchor_date)}`} />
        <StatTile label="Workers, trailing 12 mo" value={num(workers12)}
          sub={`${activeStates.size} states reporting`} />
      </div>

      <SectionHeading
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
          <SectionHeading>Workers affected by state · trailing 12 months</SectionHeading>
          <Choropleth values={mapValues} activeStates={activeStates}
            label="workers affected, trailing 12 mo" />
        </div>
        <div className="lg:col-span-2">
          <SectionHeading>Largest employers · trailing 12 months</SectionHeading>
          <table className="w-full text-sm">
            <tbody>
              {national.top_employers_12mo.slice(0, 12).map((e, i) => (
                <tr key={e.employer} className="border-b border-rule">
                  <td className="tabular text-xs text-ink-faint py-1.5 pr-2 w-6">{i + 1}</td>
                  <td className="py-1.5 pr-3 font-serif">{e.employer}</td>
                  <td className="tabular text-right py-1.5">{num(e.workers)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <SectionHeading>Largest notices · trailing 90 days</SectionHeading>
      <NoticeTable notices={national.biggest_recent.slice(0, 15)} />

      <SectionHeading>Coverage</SectionHeading>
      <p className="text-sm font-serif text-ink-muted max-w-3xl leading-relaxed">
        This register consolidates notices from {activeStates.size} state portals with automated
        collection; {Object.values(meta.states).filter((s) => s.status === "manual_only").length}{" "}
        states publish nothing online and are absent. History depth varies by state — Illinois
        reaches back to 1987 while some portals expose only the current year. See each{" "}
        <Link to="/states" className="underline hover:text-ink">state profile</Link> for source
        details and coverage depth.
      </p>
    </div>
  );
}
