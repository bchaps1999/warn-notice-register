# WARN Notice Register

A consolidated, normalized, deduplicated dataset of WARN Act layoff notices,
refreshed automatically from ~47 state portals.

## What this is

The federal WARN Act requires employers to give 60 days' notice of qualifying
plant closings and mass layoffs. Notices are filed with state agencies; there
is no national feed. This pipeline scrapes every state that publishes online,
normalizes each state's idiosyncratic format into one canonical schema,
deduplicates and version-tracks notices, and commits the results here:

- `data/warn.sqlite.gz` — the full database, gzipped (notices, versions, run telemetry)
- `data/exports/warn_notices.csv` — one row per notice, all active states
- `data/exports/states/{xx}.csv` — per-state cuts
- `data/exports/notice_links.csv` — detected revision/duplicate links between notices
- `data/health/health.md` — per-state pipeline health, updated every run
- `data/health/dupes_review.csv` — gray-zone duplicate candidates for human review

## Data dictionary (`warn_notices.csv`)

| Column | Meaning |
|---|---|
| `state` | Two-letter postal code |
| `employer_name` | Employer as reported by the state |
| `location` | City/location string as reported (formats vary by state) |
| `notice_date` | Date the notice was filed/received (ISO-8601) |
| `effective_date` | First layoff/closure date (ISO-8601) |
| `employees_affected` | Reported headcount (null when the state omits it) |
| `layoff_type` | `closure`, `mass_layoff`, or `unknown` |
| `is_temporary` | 1 if the state flagged it temporary, else 0/blank |
| `is_amendment` | Source flagged this filing as amending an earlier one |
| `is_amended` | We have observed more than one version of this notice |
| `current_version` | Version count (see `notice_versions` in SQLite for history) |
| `source_url` | The state portal the record came from |
| `source_notice_id` | Content hash from the normalizer (not stable across amendments) |
| `dedupe_key` | sha1(state \| normalized employer \| notice_date \| normalized location) |
| `first_seen` / `last_seen` | When this pipeline first/last observed the notice |

Caveats: states disagree about what counts as a notice, how amendments are
reported, and how employees are counted. Four states (AR, NH, WV, WY) publish
nothing online and are absent. Coverage per state starts at whatever history
its portal exposes; see `data/health/status.json` for per-state status.

## How it works

```
fetch (per state)  ->  normalize  ->  verify  ->  ingest (SQLite)  ->  export
```

- **Fetch**: [biglocalnews/warn-scraper](https://github.com/biglocalnews/warn-scraper)
  per-state scrapers (SHA-pinned), with local overrides in
  `warnlive/fetch/patches/` and adapters for states it lacks (MA, MN, NC, NV)
  in `warnlive/fetch/custom/`.
- **Normalize**: wraps [biglocalnews/warn-transformer](https://github.com/biglocalnews/warn-transformer)
  per-state transformers row-by-row with error capture; custom transformers in
  `warnlive/normalize/custom/`. Unmapped raw columns are preserved as JSON.
- **Verify**: every scrape of every state is independently checked — fetch
  success, row counts, header-drift against a snapshot, parse-failure rate,
  employer coverage, date sanity, freshness, duplicate-key rate. A state that
  fails does not ingest; the health report says why.
- **Revision/duplicate links**: beyond the exact dedupe key, `warnlive dupes`
  links notices that are revisions or likely duplicates of one another —
  name markers ("(Amended)", "2nd notice"), source-declared amendments,
  same-employer refilings within 45 days at the same location, and fuzzy
  spelling variants — scored with location, effective-date, and headcount
  evidence. Notices are **linked, never merged**: `notice_links` in SQLite,
  `notice_links.csv` in exports. Names with conflicting site identifiers
  (store numbers, warehouse codes, roman numerals) are never linked, and
  gray-zone pairs go to `dupes_review.csv` instead of the table.
- **Registry**: `warnlive/states.yaml` is the single source of truth for each
  state's adapter, thresholds, cadence, and human-controlled status
  (`unverified` → `active` / `broken`). Only active states enter exports.

Both BLN projects are Apache-2.0; this project builds on their work with
gratitude.

## Running it

```bash
./install.sh                     # venv + pinned deps (see script for why it's not plain pip)
source .venv/bin/activate
warnlive verify ct               # live-check one state, no DB writes
warnlive scrape ct il nj         # scrape, verify, ingest, export
warnlive scrape --cadence weekly # everything active
warnlive backfill                # historical data from BLN's warn-github-flow
warnlive report --gh-issues      # open/close per-state health issues (CI)
```

Scheduled runs: `.github/workflows/scrape-daily.yml` (high-volume states) and
`scrape-weekly.yml` (full sweep, Sundays). Optional secret `ZYTE_API_KEY`
enables the Zyte proxy for states behind aggressive bot protection (LA, TX
fallback, MA fallback).

`warnlive edgar-refresh` (manual, occasional) rebuilds the SEC EDGAR
name→CIK reference used to derive the export's `cik`/`ticker`/`cik_match`
columns. It requires `SEC_EDGAR_UA` set to a declared user agent per SEC
fair-access policy, e.g. `SEC_EDGAR_UA="Your Name you@example.com"`.
Scheduled runs never contact the SEC — they read the committed reference
file at `data/reference/edgar_names.csv.gz`.
