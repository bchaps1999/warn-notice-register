import { Link } from "react-router-dom";
import { useMeta } from "../lib/hooks";
import { date, num, STATE_NAMES } from "../lib/format";
import { Callout } from "../components/ui/Callout";
import { DataTable, type Column } from "../components/ui/DataTable";
import { SectionHeading } from "../components/ui/SectionHeading";
import { ErrorNote, Skeleton } from "../components/ui/Skeleton";
import { StatTile } from "../components/ui/StatTile";
import type { StateCoverage } from "../lib/types";

type Row = StateCoverage & { postal: string };

function pct(part: number, whole: number) {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : "—";
}

export function Methods() {
  const { data: meta, error } = useMeta();
  if (error) return <ErrorNote message={error} />;
  if (!meta) return <Skeleton lines={10} />;

  const t = meta.totals;
  const rows: Row[] = Object.entries(meta.states)
    .map(([postal, s]) => ({ postal, ...s }))
    .sort((a, b) => b.notices - a.notices);
  const manual = rows.filter((r) => r.status === "manual_only" || r.source === "manual");
  const archiveStates = rows.filter((r) => r.archived > 0);

  const columns: Column<Row>[] = [
    {
      key: "state",
      header: "State",
      render: (r) => (
        <Link to={`/states/${r.postal.toLowerCase()}`} className="hover:underline">
          {STATE_NAMES[r.postal] ?? r.name}
        </Link>
      ),
    },
    {
      key: "notices",
      header: "Notices",
      numeric: true,
      render: (r) => num(r.notices),
    },
    {
      key: "range",
      header: "History",
      numeric: true,
      render: (r) => (
        <span className="text-xs">
          {r.first ? `${r.first.slice(0, 4)}–${(r.last ?? "").slice(0, 4)}` : "—"}
        </span>
      ),
    },
    {
      key: "undated",
      header: "No notice date",
      numeric: true,
      render: (r) => (
        <span className={r.undated ? "text-oxide" : "text-ink-faint"}>
          {pct(r.undated, r.notices)}
        </span>
      ),
    },
    {
      key: "no_jobs",
      header: "No headcount",
      numeric: true,
      render: (r) => (
        <span className={r.no_jobs ? "text-oxide" : "text-ink-faint"}>
          {pct(r.no_jobs, r.notices)}
        </span>
      ),
    },
    {
      key: "archived",
      header: "From archives",
      numeric: true,
      render: (r) => (
        <span className="text-ink-muted">{r.archived ? pct(r.archived, r.notices) : "—"}</span>
      ),
    },
    {
      key: "identified",
      header: "Employer identified",
      numeric: true,
      render: (r) => (
        <span className="text-ink-muted">{pct(r.identified, r.notices)}</span>
      ),
    },
  ];

  return (
    <div>
      <p className="smallcaps text-[10px] text-ink-muted">Methods</p>
      <h2 className="font-display text-3xl mt-1">How this register is built</h2>
      <p className="text-sm font-serif text-ink-muted mt-2 max-w-2xl leading-relaxed">
        The WARN Act requires 60 days' notice of qualifying plant closings and
        mass layoffs. Notices go to state agencies, and there is no national
        feed — so this register is assembled from every state that publishes
        online. What follows is what that assembly does and does not capture.
      </p>

      <SectionHeading>What is in the data</SectionHeading>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
        <StatTile label="Notices" value={num(t.notices)}
          sub={meta.date_range ? `since ${date(meta.date_range.min)}` : undefined} />
        <StatTile label="States collected" value={String(t.states)}
          sub={`${manual.length} publish nothing online`} />
        <StatTile label="Employer identified" value={pct(t.identified, t.notices)}
          sub="matched to a company register" />
        <StatTile label="Industry recorded" value={pct(t.with_industry, t.notices)}
          sub="published or derived" />
      </div>

      <SectionHeading sub="Every figure on this site inherits these gaps. They are properties of what states publish, not of the collection.">
        What is missing
      </SectionHeading>
      <div className="grid sm:grid-cols-3 gap-6 text-sm font-serif">
        <div>
          <p className="tabular text-2xl">{pct(t.undated, t.notices)}</p>
          <p className="smallcaps text-[10px] text-ink-muted mt-1">No notice date</p>
          <p className="text-ink-muted mt-1.5 leading-relaxed">
            {num(t.undated)} notices carry no filing date. Some portals publish
            only the layoff date; California's archived reports never carried
            one. Those notices are placed by their effective date instead, and
            still appear in every total.
          </p>
        </div>
        <div>
          <p className="tabular text-2xl">{pct(t.no_jobs, t.notices)}</p>
          <p className="smallcaps text-[10px] text-ink-muted mt-1">No headcount</p>
          <p className="text-ink-muted mt-1.5 leading-relaxed">
            {num(t.no_jobs)} notices report no number of workers. They count as
            notices everywhere on this site and contribute nothing to worker
            totals, so worker figures are floors, not estimates.
          </p>
        </div>
        <div>
          <p className="tabular text-2xl">{pct(t.archived, t.notices)}</p>
          <p className="smallcaps text-[10px] text-ink-muted mt-1">From archived pages</p>
          <p className="text-ink-muted mt-1.5 leading-relaxed">
            {num(t.archived)} notices come from state documents their agencies
            no longer publish, recovered through the Internet Archive. Each one
            links to the exact archived artifact it was read from.
          </p>
        </div>
      </div>

      <Callout title="Counts are not comparable across states">
        States disagree about what a notice is. Some file one notice per
        location and some per company; some publish amendments as new rows and
        some overwrite. This register links revisions rather than merging them,
        so a company that amends a filing appears more than once by design —
        the notice pages show what is linked to what.
      </Callout>

      <SectionHeading
        sub="Sorted by volume. History depth is set by whatever each portal exposes, not by when layoffs happened."
        right={
          <Link to="/states" className="smallcaps text-[10px] text-oxide hover:underline">
            State profiles →
          </Link>
        }
      >
        Coverage by state
      </SectionHeading>
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.postal} />

      {manual.length > 0 && (
        <Callout title="States absent entirely">
          {manual.map((r) => STATE_NAMES[r.postal] ?? r.name).join(", ")} publish
          no notice-level list online. Their notices exist only through records
          requests and are not in this register at any count.
        </Callout>
      )}

      <SectionHeading>How employers are identified</SectionHeading>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed">
        Notices name employers as the filer wrote them. To group filings across
        states and spellings, each is matched against public company registers:
        SEC filers by name and filing era, IRS exempt organizations by name and
        state, legal-entity identifiers, and Wikidata. Where a company is
        somebody's subsidiary, the parent's own SEC filings say so.
      </p>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed mt-3">
        Every match must be exact after normalization, agree with a second
        attribute where one exists, and leave exactly one candidate standing.
        Ambiguity matches nothing, which is why{" "}
        {pct(t.notices - t.identified, t.notices)} of notices carry no company
        identifier: most are single-location businesses in no public register,
        and a wrong identifier would be worse than none.
      </p>

      <SectionHeading>Provenance</SectionHeading>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed">
        Every notice records the page or document it came from, when it was
        first seen, and when it was last confirmed at the source. Nothing is
        merged: notices that look like revisions or duplicates of each other are
        linked, with the reason recorded, and both remain readable. The whole
        database, per-state extracts, and the link table are published as{" "}
        <a
          href="https://github.com/bchaps1999/warn-notice-register"
          className="underline hover:text-ink"
          target="_blank" rel="noreferrer"
        >
          CSV and SQLite
        </a>
        .
      </p>
      <p className="text-xs text-ink-faint font-serif mt-6">
        Built {date(meta.built_at)} · {archiveStates.length} states include
        archive-recovered history.
      </p>
    </div>
  );
}
