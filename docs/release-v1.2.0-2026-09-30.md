# WARN Notice Register v1.2.0: entries, addresses, and recovered sources

v1.2.0 contains **80,703 admitted notices** (95,090 versions), reporting
**8,205,189 affected workers**. v1.1.1 had 74,828 notices and 7,815,994
workers. It is a full replay of a new dated
[source bundle](../data/source_snapshots/2026-09-30-v1.2-source-bundle.tar.gz),
built from fresh 2026-09-30 captures where they keep every released notice.
The replay applies:

- corrected parsing,
- a revised unit of count,
- restored site addresses,
- sources recovered from official agency pages.

The [candidate record](candidate-v1.2-2026-09-30.md) has the exact commands,
hashes, per-state tables, and checks. Counts are admitted source entries, not
an estimate of every WARN filing nationally.

## What changed for users of the data

### Unit of count

- **One notice is one entry the agency lists.**
  - **Sources with a filing ID** (Oregon WARN#, New York Control Number,
    Illinois IEBS, Kansas): the filing is the notice, and its itemized sites
    or phases are listed inside it.
  - **Sources without one** (California EDD reports, the New York dashboard,
    Iowa's event log, Wisconsin, Pennsylvania and Michigan logs): each listed
    row is a notice. Rows from one filing share
    `source_details.filing_group` and a `sibling_entry` link.
- **California and New York.** Distinct rows of one document that shared a
  key had been folded silently into another notice's versions; they are now
  separate entries.
  - California: +3,317 notices. Example: an EDD report printing 9, 41 and 53
    workers for three Temecula lines now gives three notices.
  - New York: +2,492, mostly multi-site dashboard filings with distinct site
    addresses.
- **Re-listings.** A notice re-listed with a corrected figure in a later
  report remains one notice, with the newest listing as its current version.
- **Recovered filings.**
  - Oregon: multi-site and phased filings are one notice, with itemized
    worker sums (+106).
  - Iowa: distinct-site entries (+161).
  - Wisconsin: phase groups.
  - Where the agency's rows do not say how figures combine (repeated site and
    date, several amendments), workers are left unknown and
    `worker_allocation` is `unresolved`.

### Removed from the notice count

- **Nebraska.** 803 rows came from NDOL's general layoff/closure report, not
  its WARN report, and are now published in `source_observations.csv` with
  status `not_in_agency_warn_report`. Pre-2020 Nebraska falls from 887 notices
  to 84.
  - This does not establish that those events were not WARN events: 108 of
    them report 50 or more workers.
  - 47 Nebraska notices were re-keyed, because the page's Location column now
    fills the location when City is blank.
- **Eleven non-notice rows.** North Carolina county-summary rows had been
  admitted as employers "0" through "6", along with New Jersey "1961" and two
  California and two Illinois agency test entries.
- **Duplicate records.**
  - 48 Wisconsin "update" rows identical to their original notice are now
    versions of it.
  - 24 New York dashboard rows became versions of the exactly matching
    Control Number filing.

The [transition maps](../data/review/) list every retired and re-keyed
dedupe key. Every other v1.1.1 key is unchanged.

### Recovered coverage

- **Wisconsin 2016–2019:** 321 rows from DWD's official year pages. The
  upstream collector requested a page that returns no tables.
- **Nebraska 2020–2022:** 29 rows from a pinned capture of NDOL's official
  WARN page.
- **Nevada 2021:** 20 rows transcribed from DETR's scanned list and verified
  cell by cell (160 of 160 cells), with the page images and hashes kept as
  [evidence](../data/source_snapshots/2026-09-30-nv-2021-transcription/).
- **Rows previously lost to one unreadable cell:** Hawaii starred amendment
  lines (+17), plus one row each in New Mexico and Rhode Island.
- **Fresh captures:** new notices through 2026-09-30 for AL, CO, DC, FL, ID,
  IN, MI, MT, NC, NJ, NM, OK, PA, SD, UT, VT, WA and WI.

### Fields

- **Site addresses.** `site_address` is filled for 22,597 notices (v1.1.1:
  6,469), and the new `site_address_basis` column says how each is known:
  - `quality_evidence` (8,270): pinned documents (California, North Carolina,
    Maryland).
  - `labeled_site_field` (12,083): labeled agency site columns (Illinois, New York,
    Georgia, South Carolina).
  - `filed_county_consistent` (250): checked against the county the agency filed
    (Pennsylvania).
  - `fl_company_cell_in_state` (1,994): Florida's company cell, when the street is in
    Florida. A 52-row letter-verified sample found these were the affected
    site in every decidable case; out-of-state street lines were headquarters
    and are excluded.

  The site's detail page now says "Reported site address", with the basis.
- **Dates.**
  - Upstream "corrections" that invented dates were removed; for example, New
    Jersey Morgan Stanley is 2023 again, and Alabama's `01/01/0001`
    placeholder is blank.
  - An unparseable date or worker count now blanks that field and keeps the
    notice.
  - Mississippi and North Dakota date/employer mix-ups are fixed.
  - A notice date later than the listing's first observation is flagged in
    `timing_qc`.
- **Layoff type and temporary flag** now come from source columns or the
  agencies' published legends: DC, MD, WI, OR, NY, IA, IN, RI, MI, and CO's
  job-loss columns.
- **Geography.**
  - Los Angeles communities and New York City boroughs resolve.
  - Unresolved California places fall from 307 to 70, and Iowa's from 42 to 3.
  - Several county names that had resolved to same-named towns are corrected.
  - Strings naming several places no longer resolve to one.
  - Every coordinate is a Census place or county interior point, not a
    geocoded address; `geo_basis=address` means only that the city was read
    from an address.
- **Other fixes:**
  - Nevada's March 2020 rows are read under their printed column labels.
  - Iowa gains locations, layoff types and industry codes.

## Reconciliation and checks

- **Released keys.** Every v1.1.1 dedupe key is present, retired or re-keyed
  through a transition map, or held by a documented non-notice rule. None is
  unexplained.
- **Row accounting.** Every source row is admitted, versioned, or recorded in
  the [exception ledger](../data/source_snapshots/2026-09-30-v1.2-candidate.exceptions.jsonl.gz)
  (5,702 rows, down from 9,668) with its reason.
- **Determinism.** Two independent replays have identical fingerprints and
  byte-identical ledgers.
- **Consistency.** The national CSV equals the sum of the state CSVs and the
  database.
- **Tests.** The full Python suite, the site tests and the site build pass.
- **Regression baseline.** Refreshed with this release. The only differences
  from the v1.1.1 baseline are the intended Nebraska count and the Iowa and
  Mississippi location fill.
- **Independent review.** Two reviews recomputed key results from source
  rows: California document grouping, the New York Rite Aid sites, Oregon
  sums, Iowa amendments, Wisconsin updates and the Nebraska report split.
  Their findings are fixed in this release.

Rebuild from the bundle with:

```bash
python -m warnlive.migrate.offline_rebuild \
  --bundle data/source_snapshots/2026-09-30-v1.2-source-bundle.tar.gz \
  --quality-evidence-dir data/source_snapshots/2026-09-24-quality-evidence \
  --db /tmp/warn-v1.2.sqlite --observed-at 2026-09-30 --source-only \
  --report /tmp/warn-v1.2-report.json --exceptions /tmp/warn-v1.2.exceptions.jsonl
```

## Known limits

- **Rolling sources.** Minnesota, Arizona, Delaware, Maine, Connecticut, New
  York's current list, Ohio, Massachusetts and Maryland only list recent
  notices. Their fresh captures would drop released rows, so this release
  keeps their frozen July captures, and scheduled scrapes add their newer
  notices after publication.
  - Texas, Illinois and Missouri keep frozen captures because they are tied
    to pinned agency artifacts.
  - Hawaii's new collector format re-keys its rows and awaits a migration.
  - Bundles that hold several dated captures per state are planned.
- **Held rather than guessed.**
  - 24 recovered New York Control Number filings may duplicate published
    dashboard notices, which carry `possible_correspondence`.
  - Five California groups re-listed across reports move the action date by
    more than 45 days.
  - Missouri's 1,284 rapid-response log rows are published as unresolved
    source observations.
- **No supported notice-level source:** Arkansas, New Hampshire, Wyoming and
  Puerto Rico. West Virginia's official listing has an unverified collector:
  most of its notice letters are scanned images awaiting transcription.
  Ohio 2023–2025 is missing pending a records request.
- **Archive states:** the Georgia, Iowa, Kentucky, Louisiana, Oregon and
  Tennessee collectors are paused until agency-only collectors exist.
- **Freshness of Massachusetts.** Scheduled runs use Internet Archive copies
  of mass.gov's files when mass.gov refuses GitHub runners. Each copy is
  recorded with its capture date, and such runs are marked degraded.
