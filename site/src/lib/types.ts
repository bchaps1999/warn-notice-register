export interface Meta {
  built_at: string;
  key_prefix_len: number;
  totals: { notices: number; workers: number; states: number };
  date_range: { min: string; max: string } | null;
  states: Record<
    string,
    {
      name: string;
      status: string;
      notices: number;
      latest_verdict: string | null;
      last_success: string | null;
    }
  >;
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

export interface National {
  anchor_date: string;
  monthly: MonthPoint[];
  top_employers_12mo: TopEmployer[];
  biggest_recent: NoticeSummary[];
  states_12mo: { state: string; notices: number; workers: number }[];
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
  coverage: { notices: number; earliest: string | null; latest: string | null };
  monthly: MonthPoint[];
  top_employers: TopEmployer[];
  top_employers_24mo: TopEmployer[];
  recent: NoticeSummary[];
}

export interface NoticeIndex {
  states: string[];
  types: string[];
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
  industry: string | null;
  naics: string | null;
  naics_basis: string | null;
  first_seen: string;
  last_seen: string;
  versions: NoticeVersion[];
  links: NoticeLink[];
}
