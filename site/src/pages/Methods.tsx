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
  const noRows = rows.filter((r) => r.notices === 0);
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
      header: "Archive links",
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
    {
      key: "placed",
      header: "Placed to a county",
      numeric: true,
      render: (r) => (
        <span className={r.placed ? "text-ink-muted" : "text-oxide"}>
          {pct(r.placed, r.notices)}
        </span>
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
        feed — so this register is assembled from available state portals and
        agency archives. Coverage differs by state and period. What follows is
        what that assembly does and does not capture.
      </p>

      <SectionHeading>What is in the data</SectionHeading>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-6">
        <StatTile label="Notices" value={num(t.notices)}
          sub={meta.date_range ? `since ${date(meta.date_range.min)}` : undefined} />
        <StatTile label="Jurisdictions in data" value={String(t.states)}
          sub={`${noRows.length} have no admitted notices here`} />
        <StatTile label="Employer identified" value={pct(t.identified, t.notices)}
          sub="matched to a company register" />
        <StatTile label="Industry recorded" value={pct(t.with_industry, t.notices)}
          sub="published or derived" />
        <StatTile label="Placed to a county" value={pct(t.placed, t.notices)}
          sub="location resolved to Census geography" />
      </div>

      <SectionHeading sub="Every figure on this site inherits gaps in published sources and in what this register can reliably interpret.">
        What is missing
      </SectionHeading>
      <div className="grid sm:grid-cols-3 gap-6 text-sm font-serif">
        <div>
          <p className="tabular text-2xl">{pct(t.undated, t.notices)}</p>
          <p className="smallcaps text-[10px] text-ink-muted mt-1">No notice date</p>
          <p className="text-ink-muted mt-1.5 leading-relaxed">
            {num(t.undated)} notices have no canonical notice date.
            Some portals publish only receipt, posting, or layoff dates; those
            dates cannot be substituted for notice. These records remain in
            notice totals when their event identity is established.
          </p>
        </div>
        <div>
          <p className="tabular text-2xl">{pct(t.no_jobs, t.notices)}</p>
          <p className="smallcaps text-[10px] text-ink-muted mt-1">No headcount</p>
          <p className="text-ink-muted mt-1.5 leading-relaxed">
            {num(t.no_jobs)} notices report no number of workers. They count as
            notices everywhere on this site and contribute nothing to worker
            totals. Worker totals sum the reported counts; they do not estimate
            missing headcounts.
          </p>
        </div>
        <div>
          <p className="tabular text-2xl">{pct(t.archived, t.notices)}</p>
          <p className="smallcaps text-[10px] text-ink-muted mt-1">Internet Archive links</p>
          <p className="text-ink-muted mt-1.5 leading-relaxed">
            {num(t.archived)} notices link to Internet Archive pages. Other
            historical agency workbooks and reports are not counted in this
            measure. Historical coverage varies by jurisdiction and period.
          </p>
        </div>
      </div>

      <Callout title="Counts are not comparable across states">
        States disagree about what a notice is. Some file one notice per
        location and some per company; some publish amendments as new rows and
        some overwrite. Exact duplicate source observations may be combined,
        while unsupported relationships between apparent revisions remain
        unlinked. Counts therefore reflect admitted source events and each
        state's reporting practice.
      </Callout>

      <Callout title="Coverage limits at the v1 release">
        The initial v1 build uses pinned agency records and conservative
        admission rules. Ohio's 2023–25 annual index is missing; Kansas and
        older Missouri and Georgia coverage remain incomplete. Georgia, Iowa,
        Kentucky, Oregon, and Tennessee are archive-only while their scheduled
        collectors are being replaced. A missing live run status means the
        frozen rebuild did not verify that collector; it is not an all-clear.
      </Callout>

      <SectionHeading
        sub="Sorted by volume. The history column uses each record's display date: notice date when established, otherwise action date."
        right={
          <Link to="/states" className="smallcaps text-[10px] text-oxide hover:underline">
            State profiles →
          </Link>
        }
      >
        Coverage by state
      </SectionHeading>
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.postal} />

      {noRows.length > 0 && (
        <Callout title="Jurisdictions without admitted notices">
          {noRows.map((r) => STATE_NAMES[r.postal] ?? r.name).join(", ")} have
          no admitted source records in this release. Some lack a usable public
          list; others have source rows whose identity or provenance remains
          unresolved. They are absent from national notice and worker totals.
        </Callout>
      )}

      <SectionHeading>How employers are identified</SectionHeading>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed">
        Notices name employers as the source published them. Separate,
        deterministic annotations may match a name to a public company or
        organization register. An identifier or current parent annotation does
        not by itself establish who owned the filing entity when the notice
        was given.
      </p>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed mt-3">
        Matching rules require a unique supported candidate and leave
        ambiguous cases unmatched. {pct(t.notices - t.identified, t.notices)}
        of notices have no company identifier. These annotations are useful for
        navigation, but should not be read as a historical ownership ledger.
      </p>

      <SectionHeading>How locations become places</SectionHeading>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed">
        States write a location however they like: a bare city, a city and its
        county, a full street address, several sites in one field. Each is
        matched against the Census rosters of places, counties and townships
        within the filing state, so a notice can carry a county FIPS code that
        joins to other federal data instead of a string that joins to nothing.
        {" "}{pct(t.placed, t.notices)} of notices reach a county.
      </p>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed mt-3">
        The rule is the one used for employers: a name that could be two places
        in the state resolves to neither, and matching never crosses a state
        line. Where a state files the county alongside the city, that county
        settles names that would otherwise be ambiguous. A city that shares its
        name with a county it is not in — Houston is in Harris County, Iowa City
        in Johnson — is placed by the city, not the coincidence.
      </p>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed mt-3">
        Some filings name no place at all and never will. Kansas, Vermont, Maine
        and Oklahoma file against workforce investment areas; other notices say
        "statewide" or "various". These are absent from every map and county
        table rather than counted as zero, which is why a county map is a view
        of {pct(t.placed, t.notices)} of the register and not all of it.
      </p>

      <SectionHeading>Provenance</SectionHeading>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed">
        Admitted notices retain a source reference and observation timestamps.
        The source-only rebuild also records excluded rows and why they were
        excluded. Apparent revisions are linked only when source evidence
        establishes the relationship. Download the{" "}
        <a
          href="https://github.com/bchaps1999/warn-notice-register/blob/main/data/exports/warn_notices.csv"
          className="underline hover:text-ink"
          target="_blank" rel="noreferrer"
        >
          notice CSV
        </a>
        {" "}or the{" "}
        <a
          href="https://github.com/bchaps1999/warn-notice-register/blob/main/data/warn.sql.gz"
          className="underline hover:text-ink"
          target="_blank" rel="noreferrer"
        >
          compressed SQL dump
        </a>
        . After cloning and running <code>./install.sh</code>, run{" "}
        <code>.venv/bin/warnlive unpack-db</code> to restore the working SQLite
        database from the dump.
      </p>
      <SectionHeading>Sources and credit</SectionHeading>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed">
        Every admitted notice traces to agency records. Some historical
        workbooks were supplied by agencies to Big Local News and remain
        available through its public mirror. Collection code builds on{" "}
        <a
          className="underline hover:text-ink"
          href="https://github.com/biglocalnews/warn-scraper"
          target="_blank" rel="noreferrer"
        >
          Big Local News&rsquo;s warn-scraper
        </a>{" "}
        (Apache-2.0), whose collectors this project extends and, for several
        states, replaces where a portal has moved or a format has changed.
      </p>
      <p className="text-sm font-serif text-ink-muted max-w-2xl leading-relaxed mt-3">
        What each state counts as a reportable layoff differs, and so does what
        it publishes about one. Totals compared across states are therefore
        comparisons of publishing practice as much as of layoffs, and the
        per-state pages say what each agency does and does not report.
      </p>
      <p className="text-xs text-ink-faint font-serif mt-6">
        Built {date(meta.built_at)} · {archiveStates.length} jurisdictions have
        at least one Internet Archive source link.
      </p>
    </div>
  );
}
