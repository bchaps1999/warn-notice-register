import { Link, useParams } from "react-router-dom";
import { useNotice } from "../lib/hooks";
import {
  NAICS_BASIS_LABEL,
  NAICS_LEVEL_LABEL,
  date,
  displayName,
  num,
  sectorLabel,
  STATE_NAMES,
  TYPE_LABEL,
} from "../lib/format";
import { SectionHeading } from "../components/ui/SectionHeading";
import { ErrorNote, Skeleton } from "../components/ui/Skeleton";
import { Stamp } from "../components/ui/Stamp";
import { NotFound } from "./NotFound";
import type { NoticeDetail as NoticeDetailData, NoticeLink } from "../lib/types";

/** The colours the Stamp component uses for each layoff type, without its
 *  border — so the type can be set like the state name and told apart by
 *  colour alone. Kept beside Stamp's TONE map in spirit; if one changes the
 *  other should. */
const TYPE_TONE: Record<string, string> = {
  closure: "text-[var(--chart-closure)]",
  mass_layoff: "text-[var(--chart-layoff)]",
  unknown: "text-ink-faint",
};

export function NoticeDetailPage() {
  const { key } = useParams();
  const { data: n, error } = useNotice(key);
  if (error === "Notice not found") return <NotFound />;
  if (error) return <ErrorNote message={error} />;
  if (!n) return <Skeleton lines={8} />;

  const amendments = n.links.filter((l) => l.kind === "amendment_of");
  const duplicates = n.links.filter((l) => l.kind === "possible_duplicate");

  return (
    <div className="max-w-3xl">
      {/* One eyebrow line: where the notice is from and what kind it is,
          set the same way and told apart by colour rather than by a box.
          They classify the notice, not the employer, so they sit above the
          company name rather than under it. The rarer flags keep their
          stamps — they are exceptions, and should look like exceptions. */}
      <div className="flex items-center gap-2 flex-wrap">
        <p className="smallcaps text-[10px] text-ink-muted">
          Notice record ·{" "}
          <Link to={`/states/${n.state.toLowerCase()}`} className="hover:underline text-oxide">
            {STATE_NAMES[n.state] ?? n.state}
          </Link>
          {" · "}
          <span className={TYPE_TONE[n.layoff_type] ?? "text-ink-faint"}>
            {TYPE_LABEL[n.layoff_type]}
          </span>
        </p>
        {n.is_temporary === 1 && <Stamp>Temporary</Stamp>}
        {n.is_amendment === 1 && <Stamp tone="oxide">Filed as amendment</Stamp>}
        {n.is_amended === 1 && <Stamp tone="oxide">Amended · v{n.current_version}</Stamp>}
      </div>
      {/* The company, not the string. States file the same firm many ways —
          "NBCUniversal Media, LLC - 1320" names a building — so the heading
          is the canonical name and the filed string is shown once below,
          where it belongs: as evidence rather than as the title. */}
      <h2 className="font-display text-3xl mt-1">
        <Link
          to={`/employers/${encodeURIComponent(n.employer_key)}`}
          className="hover:underline"
          title="All notices from this employer"
        >
          {displayName(n.canonical_name) || n.employer_name}
        </Link>
      </h2>
      {n.canonical_name && n.employer_name !== n.canonical_name && (
        <p className="text-sm text-ink-muted font-serif mt-1">
          Filed as “{n.employer_name}”
        </p>
      )}

      <SectionHeading>Filing</SectionHeading>
      <dl className="grid grid-cols-[11rem_1fr] gap-y-2 text-sm">
        <Row label="Location">{n.location ?? "—"}</Row>
        {n.county_fips && (
          <Row label="Resolved place">
            <Link
              to={`/explore?state=${n.state}&county=${n.county_fips}`}
              className="hover:underline"
            >
              {n.place_name ? `${n.place_name}, ` : ""}
              {n.county_name}
            </Link>
            <span className="tabular text-xs text-ink-muted ml-2">
              FIPS {n.county_fips}
              {n.geo_basis === "county" && " · county only"}
              {n.geo_basis === "subdivision" && " · township"}
            </span>
          </Row>
        )}
        <Row label="Notice date">{date(n.notice_date)}</Row>
        <Row label="Layoff/closure date">{date(n.effective_date)}</Row>
        <Row label="Workers affected">
          <span className="tabular">{num(n.employees_affected)}</span>
        </Row>
        {(n.industry || n.naics) && <IndustryRow n={n} />}
      </dl>

      {(n.cik || n.ein || n.lei || n.wikidata_qid || n.parent_company) && (
        <>
          <SectionHeading>
            {n.cik ? "Public company" : n.ein ? "Nonprofit organization" : "Company identity"}
          </SectionHeading>
          <dl className="grid grid-cols-[11rem_1fr] gap-y-2 text-sm">
            {n.cik && (
              <Row label="SEC CIK">
                <a
                  href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${n.cik}&type=&dateb=&owner=include&count=40`}
                  className="underline hover:text-ink tabular"
                  target="_blank" rel="noreferrer"
                >
                  {String(n.cik).padStart(10, "0")}
                </a>
                <span className="text-ink-faint text-xs ml-2">
                  filing history around this notice
                </span>
              </Row>
            )}
            {n.ticker && (
              <Row label="Ticker">
                <a
                  href={`https://finance.yahoo.com/quote/${n.ticker}`}
                  className="underline hover:text-ink tabular"
                  target="_blank" rel="noreferrer"
                >
                  {n.ticker}
                </a>
                <span className="text-ink-faint text-xs ml-2">price chart</span>
              </Row>
            )}
            {n.sic_description && (
              <Row label="SEC industry">
                {n.sic_description}
                {n.sic && <span className="tabular text-xs text-ink-muted ml-2">SIC {n.sic}</span>}
                <span className="block text-xs text-ink-faint">
                  assigned to the filer, so it describes the whole company
                </span>
              </Row>
            )}
            {n.ein && (
              <Row label="IRS EIN">
                <a
                  href={`https://projects.propublica.org/nonprofits/organizations/${n.ein}`}
                  className="underline hover:text-ink tabular"
                  target="_blank" rel="noreferrer"
                >
                  {n.ein}
                </a>
                <span className="text-ink-faint text-xs ml-2">Form 990 filings</span>
              </Row>
            )}
            {n.lei && (
              <Row label="Legal entity ID">
                <a
                  href={`https://search.gleif.org/#/record/${n.lei}`}
                  className="underline hover:text-ink tabular"
                  target="_blank" rel="noreferrer"
                >
                  {n.lei}
                </a>
              </Row>
            )}
            {n.parent_company && (
              <Row label="Parent company">
                {n.parent_cik ? (
                  <a
                    href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${n.parent_cik}&type=&dateb=&owner=include&count=40`}
                    className="underline hover:text-ink"
                    target="_blank" rel="noreferrer"
                  >
                    {displayName(n.parent_company)}
                  </a>
                ) : (
                  displayName(n.parent_company)
                )}
                {n.parent_cik && (
                  <span className="text-ink-faint text-xs ml-2">
                    listed this employer as a subsidiary
                  </span>
                )}
              </Row>
            )}
            {n.wikidata_qid && (
              <Row label="Wikidata">
                <a
                  href={`https://www.wikidata.org/wiki/${n.wikidata_qid}`}
                  className="underline hover:text-ink tabular"
                  target="_blank" rel="noreferrer"
                >
                  {n.wikidata_qid}
                </a>
              </Row>
            )}
            {(n.cik_match || n.wikidata_match) && (
              <Row label="How it matched">
                <span className="text-xs text-ink-muted">
                  {matchExplanation(n.cik_match ?? n.wikidata_match)}
                </span>
              </Row>
            )}
          </dl>
        </>
      )}

      {n.versions.length > 1 && (
        <>
          <SectionHeading>Version history</SectionHeading>
          <ol className="space-y-4">
            {[...n.versions].reverse().map((v) => (
              <li key={v.version} className="border-l-2 border-rule pl-4">
                <p className="smallcaps text-[10px] text-ink-muted">
                  Version {v.version} · observed {date(v.observed_at)}
                </p>
                <dl className="grid grid-cols-[11rem_1fr] gap-y-1 text-sm mt-1">
                  {["employer_name", "location", "notice_date", "effective_date", "employees_affected", "layoff_type"].map(
                    (f) =>
                      v.fields[f] !== undefined && (
                        <Row key={f} label={f.replace(/_/g, " ")}>
                          {String(v.fields[f] ?? "—")}
                        </Row>
                      )
                  )}
                </dl>
              </li>
            ))}
          </ol>
        </>
      )}

      {amendments.length > 0 && <LinkList title="Amendment chain" links={amendments} />}
      {duplicates.length > 0 && <LinkList title="Possible duplicates" links={duplicates} />}

      <Provenance n={n} />
    </div>
  );
}

/** Where this record came from, in a sentence.
 *
 *  This was four labelled rows and a heading, which is a lot of furniture
 *  for "we read it off a website". Worse, two of the rows were usually the
 *  same date printed twice: a notice seen once has first_seen == last_seen,
 *  and a reader gains nothing from being shown it under two names.
 *
 *  The dates only say something when they differ, and then what they say is
 *  how long the state kept it posted. So they are written as a span when
 *  there is a span and as one date when there is not. The record key is a
 *  hash for joining exports, not a fact about the layoff, so it moves in
 *  with the raw row where the rest of the plumbing lives.
 */
function Provenance({ n }: { n: NoticeDetailData }) {
  const from = date(n.first_seen);
  const to = date(n.last_seen);
  const portal = n.source_url ? (
    <a href={n.source_url} className="underline hover:text-ink" target="_blank" rel="noreferrer">
      {STATE_NAMES[n.state] ?? n.state} state portal
    </a>
  ) : (
    `${STATE_NAMES[n.state] ?? n.state} state portal`
  );
  return (
    <>
      <SectionHeading>Provenance</SectionHeading>
      <p className="text-xs text-ink-faint font-serif">
        Collected from the {portal}
        {from && to && from !== to ? `, listed ${from} to ${to}.` : from ? ` on ${from}.` : "."}
      </p>
      <RawExtra n={n} />
    </>
  );
}

/** What the employer does, from both authorities, each labelled.
 *
 *  Two industries used to appear in two distant sections with nothing
 *  saying they describe different things. A state classifies the site that
 *  filed; the SEC classifies the whole registrant. For a studio lot filing
 *  under Comcast's registrant those are genuinely different answers, and a
 *  reader comparing them without being told is entitled to conclude one is
 *  wrong. Where the code has no title in any reference file we hold, its
 *  sector is named rather than printing an em-dash beside a number. */
function IndustryRow({ n }: { n: NoticeDetailData }) {
  const sector = sectorLabel(n.naics);
  const basis = n.naics_basis ? NAICS_BASIS_LABEL[n.naics_basis] : null;
  const level = n.naics_level ? NAICS_LEVEL_LABEL[n.naics_level] : null;
  return (
    <Row label="Industry">
      {n.industry || sector || "—"}
      {n.naics && (
        <span className="tabular text-xs text-ink-muted ml-2">NAICS {n.naics}</span>
      )}
      {(basis || level) && (
        <span className="block text-xs text-ink-faint">
          {basis}
          {basis && level && " · describes "}
          {!basis && level && "Describes "}
          {level}
        </span>
      )}
    </Row>
  );
}

/** The matcher's method, in words.
 *
 *  These strings are pipeline vocabulary — "exact:base", "listed-extension",
 *  "fuzzy:0.96" — and were being printed at a reader verbatim. They say
 *  something worth knowing, which is how much the match rests on, so they
 *  are translated rather than hidden. Compound methods are read left to
 *  right: "exact:post-era:base" is an exact match, on the company part of
 *  the name, to a registrant that had stopped filing by then. */
function matchExplanation(method: string | null): string {
  if (!method) return "";
  const parts = method.split(":");
  const how = parts[0].startsWith("fuzzy")
    ? `near-identical name (${Math.round(Number(parts[0].split("fuzzy")[1] || 0) * 100)}% similar)`
    : {
        exact: "exact name match",
        "listed-extension": "the one listed company whose name extends this one",
        suffix: "the name plus a generic word",
        label: "a name Wikidata records for this company",
      }[parts[0]] ?? parts[0];
  const notes = parts.slice(1).map(
    (p) =>
      ({
        base: "after setting aside the site in the filed name",
        listed: "choosing the listed filer among several of the name",
        "post-era": "to a registrant that had stopped filing by then",
      }[p] ?? p)
  );
  return [how, ...notes].join(", ");
}

function LinkList({ title, links }: { title: string; links: NoticeLink[] }) {
  return (
    <>
      <SectionHeading>{title}</SectionHeading>
      <ul className="space-y-2">
        {links.map((l, i) => (
          <li key={i} className="text-sm font-serif flex items-baseline gap-2 flex-wrap">
            <span className="text-ink-faint text-xs smallcaps">
              {l.direction === "to" ? (l.kind === "amendment_of" ? "amends" : "duplicates") : (l.kind === "amendment_of" ? "amended by" : "duplicated by")}
            </span>
            <Link to={`/notice/${l.related.key}`} className="underline hover:text-oxide">
              {l.related.employer}
            </Link>
            <span className="tabular text-xs text-ink-muted">
              {date(l.related.notice_date)} · {num(l.related.jobs)} workers
            </span>
            <span className="text-[10px] text-ink-faint">
              ({l.method}, confidence {Math.round(l.score * 100)}%)
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

/** The state's own row, exactly as it arrived.
 *
 *  Folded away rather than trimmed. Most of it does repeat the page above —
 *  the address, the company, both dates, the headcount — but this block is
 *  not here to be read, it is here so that anyone doubting a derived value
 *  can see what it was derived from. Showing a subset would defeat that,
 *  and counting the duplicates at the reader only draws attention to
 *  something they closed by not opening it.
 *
 *  The record key rides along at the end: it is a hash for joining exports,
 *  which is the same kind of thing as the rest of this. */
function RawExtra({ n }: { n: NoticeDetailData }) {
  const raw = n.versions.at(-1)?.fields.raw_extra as string | undefined;
  let parsed: Record<string, unknown> | null = null;
  try {
    parsed = raw ? JSON.parse(raw) : null;
  } catch {
    parsed = null;
  }
  if (!parsed || Object.keys(parsed).length === 0)
    return <p className="text-sm text-ink-faint font-serif">Original source row unavailable.</p>;

  return (
    <details className="mt-3">
      <summary className="text-xs smallcaps text-ink-muted cursor-pointer hover:text-ink">
        As filed by the state
      </summary>
      <dl className="grid grid-cols-[minmax(8rem,14rem)_1fr] gap-y-1.5 text-xs border border-rule p-4 bg-surface mt-2">
        {Object.entries(parsed).map(([k, v]) => (
          <Row key={k} label={k}>
            {String(v ?? "") || "—"}
          </Row>
        ))}
        <Row label="record key">
          <span className="tabular text-ink-muted">{n.dedupe_key}</span>
        </Row>
      </dl>
    </details>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="smallcaps text-[10px] text-ink-muted pt-0.5">{label}</dt>
      <dd className="font-serif break-words">{children}</dd>
    </>
  );
}
