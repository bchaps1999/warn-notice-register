import { Link, useParams } from "react-router-dom";
import { useNotice } from "../lib/hooks";
import { date, displayName, num, STATE_NAMES, TYPE_LABEL } from "../lib/format";
import { SectionHeading } from "../components/ui/SectionHeading";
import { ErrorNote, Skeleton } from "../components/ui/Skeleton";
import { Stamp } from "../components/ui/Stamp";
import { NotFound } from "./NotFound";
import type { NoticeLink } from "../lib/types";

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
      <p className="smallcaps text-[10px] text-ink-muted">
        Notice record ·{" "}
        <Link to={`/states/${n.state.toLowerCase()}`} className="hover:underline text-oxide">
          {STATE_NAMES[n.state] ?? n.state}
        </Link>
      </p>
      <h2 className="font-display text-3xl mt-1">
        <Link
          to={`/employers/${encodeURIComponent(n.employer_key)}`}
          className="hover:underline"
          title="All notices from this employer"
        >
          {n.employer_name}
        </Link>
      </h2>
      <div className="flex gap-2 mt-3">
        <Stamp tone={n.layoff_type}>{TYPE_LABEL[n.layoff_type]}</Stamp>
        {n.is_temporary === 1 && <Stamp>Temporary</Stamp>}
        {n.is_amendment === 1 && <Stamp tone="oxide">Filed as amendment</Stamp>}
        {n.is_amended === 1 && <Stamp tone="oxide">Amended · v{n.current_version}</Stamp>}
      </div>

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
        {(n.industry || n.naics) && (
          <Row label="Industry (source)">
            {n.industry ?? "—"}
            {n.naics && <span className="tabular text-xs text-ink-muted ml-2">NAICS {n.naics}</span>}
          </Row>
        )}
        <Row label="First observed">{date(n.first_seen)}</Row>
        <Row label="Last seen at source">{date(n.last_seen)}</Row>
        <Row label="Source">
          {n.source_url ? (
            <a href={n.source_url} className="underline hover:text-ink" target="_blank" rel="noreferrer">
              state portal
            </a>
          ) : (
            "—"
          )}
        </Row>
        <Row label="Record key">
          <span className="tabular text-xs">{n.dedupe_key}</span>
        </Row>
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
              <Row label="SIC industry">
                {n.sic_description}
                {n.sic && <span className="tabular text-xs text-ink-muted ml-2">SIC {n.sic}</span>}
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
              <Row label="Name match">
                <span className="tabular text-xs">
                  {n.cik_match ?? `${n.wikidata_match} (wikidata)`}
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

      <SectionHeading>As filed</SectionHeading>
      <RawExtra n={n} />
    </div>
  );
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

function RawExtra({ n }: { n: { versions: { fields: { raw_extra?: string } }[] } }) {
  const raw = n.versions.at(-1)?.fields.raw_extra;
  let parsed: Record<string, unknown> | null = null;
  try {
    parsed = raw ? JSON.parse(raw) : null;
  } catch {
    parsed = null;
  }
  if (!parsed || Object.keys(parsed).length === 0)
    return <p className="text-sm text-ink-faint font-serif">Original source row unavailable.</p>;
  return (
    <dl className="grid grid-cols-[minmax(8rem,14rem)_1fr] gap-y-1.5 text-xs border border-rule p-4 bg-surface">
      {Object.entries(parsed).map(([k, v]) => (
        <Row key={k} label={k}>
          {String(v ?? "") || "—"}
        </Row>
      ))}
    </dl>
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
