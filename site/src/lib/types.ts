export interface StateCoverage {
  name: string;
  status: string;
  notices: number;
  latest_verdict: string | null;
  last_success: string | null;
  source: string | null;
  first: string | null;
  last: string | null;
  undated: number;
  no_jobs: number;
  no_location: number;
  archived: number;
  identified: number;
  placed: number;
}

export interface Meta {
  built_at: string;
  key_prefix_len: number;
  totals: {
    notices: number;
    workers: number;
    states: number;
    undated: number;
    no_jobs: number;
    no_location: number;
    archived: number;
    identified: number;
    with_industry: number;
    placed: number;
  };
  date_range: { min: string; max: string } | null;
  states: Record<string, StateCoverage>;
}

export interface MonthPoint {
  month: string;
  notices: number;
  workers: number;
  by_type: { closure: number; mass_layoff: number; unknown: number };
}

export interface NoticeSummary {
  key: string;
  state: string;
  employer: string;
  location: string | null;
  notice_date: string | null;
  effective_date: string | null;
  jobs: number | null;
  type: string;
}

export interface TopEmployer {
  employer: string;
  key: string;
  notices: number;
  workers: number;
}

export interface SectorPoint {
  sector: string | null;
  label: string;
  notices: number;
  workers: number;
}

export interface National {
  anchor_date: string;
  monthly: MonthPoint[];
  top_employers_12mo: TopEmployer[];
  biggest_recent: NoticeSummary[];
  states_12mo: { state: string; notices: number; workers: number }[];
  counties_12mo: CountyPoint[];
  placed_12mo: number;
  sectors_12mo: SectorPoint[];
  prior_12mo: { notices: number; workers: number; sectors: SectorPoint[] };
}

export interface CountyPoint {
  fips: string;
  county: string;
  state: string;
  notices: number;
  workers: number;
}

export interface Sector {
  code: string;
  label: string;
}

export interface EmployerIndex {
  sectors: Sector[];
  total_employers: number;
  listed: number;
  min_notices: number;
  min_workers: number;
  columns: {
    key: string[];
    label: string[];
    notices: number[];
    workers: number[];
    states: string[][];
    sector: (string | null)[];
    parent: (string | null)[];
    identified: number[];
    first_date: (string | null)[];
    last_date: (string | null)[];
  };
}

export interface StateData {
  state: string;
  name: string;
  source: {
    kind: string;
    status: string;
    url: string | null;
    cadence: string | null;
    notes: string;
  };
  health: {
    latest_verdict: string | null;
    latest_run: string | null;
    latest_error: string | null;
    last_success: string | null;
    consecutive_failures: number;
  };
  coverage: {
    notices: number;
    earliest: string | null;
    latest: string | null;
    placed: number;
  };
  counties: CountyPoint[];
  monthly: MonthPoint[];
  top_employers: TopEmployer[];
  top_employers_24mo: TopEmployer[];
  recent: NoticeSummary[];
}

export interface NoticeIndex {
  states: string[];
  types: string[];
  sectors: Sector[];
  counties: { fips: string; name: string; state: string }[];
  count: number;
  columns: {
    key: string[];
    state: number[];
    date: (string | null)[];
    employer: string[];
    location: (string | null)[];
    jobs: (number | null)[];
    type: number[];
    flags: number[];
    sector: number[];
    county: number[];
  };
}

export const FLAG_TEMPORARY = 1;
export const FLAG_AMENDMENT = 2;
export const FLAG_AMENDED = 4;
export const FLAG_HAS_LINKS = 8;
export const FLAG_PUBLIC = 16;

export interface EmployerDetail {
  key: string;
  label: string;
  aliases: string[];
  cik: number | null;
  ticker: string | null;
  ein: string | null;
  lei: string | null;
  wikidata_qid: string | null;
  parent_company: string | null;
  sic_description: string | null;
  totals: { notices: number; workers: number; states: string[] };
  first_date: string | null;
  last_date: string | null;
  notices: NoticeSummary[];
}

export interface NoticeVersion {
  version: number;
  observed_at: string;
  fields: Record<string, unknown> & { raw_extra?: string };
}

export interface NoticeLink {
  direction: "to" | "from";
  kind: string;
  score: number;
  method: string;
  detail: string | null;
  related: NoticeSummary;
}

export interface NoticeDetail {
  key: string;
  dedupe_key: string;
  state: string;
  employer_name: string;
  location: string | null;
  notice_date: string | null;
  effective_date: string | null;
  employees_affected: number | null;
  layoff_type: string;
  is_temporary: number | null;
  is_amendment: number;
  is_amended: number;
  current_version: number;
  source_url: string | null;
  source_notice_id: string | null;
  cik: number | null;
  ticker: string | null;
  cik_match: string | null;
  sic: string | null;
  sic_description: string | null;
  ein: string | null;
  ntee: string | null;
  lei: string | null;
  wikidata_qid: string | null;
  wikidata_match: string | null;
  parent_company: string | null;
  parent_cik: number | null;
  employer_key: string;
  place_name: string | null;
  place_fips: string | null;
  county_name: string | null;
  county_fips: string | null;
  latitude: string | null;
  longitude: string | null;
  geo_basis: string | null;
  industry: string | null;
  naics: string | null;
  naics_basis: string | null;
  naics_level: string | null;
  canonical_name: string | null;
  canonical_basis: string | null;
  first_seen: string;
  last_seen: string;
  versions: NoticeVersion[];
  links: NoticeLink[];
}
