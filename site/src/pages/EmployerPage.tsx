import { useParams } from "react-router-dom";
import { useEmployer } from "../lib/hooks";
import { date, displayName, num } from "../lib/format";
import { NoticeTable } from "../components/ui/NoticeTable";
import { SectionHeading } from "../components/ui/SectionHeading";
import { ErrorNote, Skeleton } from "../components/ui/Skeleton";
import { StatTile } from "../components/ui/StatTile";
import { NotFound } from "./NotFound";
import type { EmployerDetail } from "../lib/types";

/** Outbound links for whichever identities resolved: SEC for registrants,
 *  the IRS 990 record for nonprofits, GLEIF for private companies. */
function identityLinks(e: EmployerDetail) {
  const links: { href: string; label: string; tabular?: boolean }[] = [];
  if (e.cik)
    links.push({
      href: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${e.cik}&type=&dateb=&owner=include&count=40`,
      label: "SEC filings",
    });
  if (e.ticker)
    links.push({
      href: `https://finance.yahoo.com/quote/${e.ticker}`,
      label: e.ticker,
      tabular: true,
    });
  if (e.ein)
    links.push({
      href: `https://projects.propublica.org/nonprofits/organizations/${e.ein}`,
      label: "Form 990 filings",
    });
  if (e.lei)
    links.push({
      href: `https://search.gleif.org/#/record/${e.lei}`,
      label: "Legal entity record",
    });
  if (e.wikidata_qid)
    links.push({
      href: `https://www.wikidata.org/wiki/${e.wikidata_qid}`,
      label: e.wikidata_qid,
      tabular: true,
    });
  return links;
}

export function EmployerPage() {
  const { key } = useParams();
  const { data: e, error } = useEmployer(key ? decodeURIComponent(key) : undefined);
  if (error === "Employer not found") return <NotFound />;
  if (error) return <ErrorNote message={error} />;
  if (!e) return <Skeleton lines={8} />;

  return (
    <div>
      <p className="smallcaps text-[10px] text-ink-muted">Employer record</p>
      <h2 className="font-display text-3xl mt-1">{displayName(e.label)}</h2>
      {e.aliases.length > 0 && (
        <p className="text-xs text-ink-faint font-serif mt-1">
          Also filed as: {e.aliases.join(" · ")}
        </p>
      )}
      <p className="text-sm font-serif text-ink-muted mt-2">
        {e.parent_company && <>Parent: <strong className="text-ink">{displayName(e.parent_company)}</strong> · </>}
        {e.sic_description && <>{e.sic_description} · </>}
        {identityLinks(e).map((link, i) => (
          <span key={link.href}>
            {i > 0 && " · "}
            <a
              href={link.href}
              className={`underline hover:text-ink${link.tabular ? " tabular" : ""}`}
              target="_blank" rel="noreferrer"
            >
              {link.label}
            </a>
          </span>
        ))}
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 mt-8">
        <StatTile label="Notices on record" value={num(e.totals.notices)} />
        <StatTile label="Workers affected" value={num(e.totals.workers)} />
        <StatTile label="States" value={String(e.totals.states.length)}
          sub={e.totals.states.slice(0, 6).join(" ") + (e.totals.states.length > 6 ? " …" : "")} />
        <StatTile label="Records span"
          value={e.first_date ? `${e.first_date.slice(0, 4)}–${e.last_date?.slice(0, 4)}` : "—"}
          sub={e.last_date ? `latest ${date(e.last_date)}` : undefined} />
      </div>

      <SectionHeading>All notices</SectionHeading>
      <NoticeTable notices={e.notices} />
    </div>
  );
}
