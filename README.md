# WARN Notice Register

A consolidated WARN Act notice dataset assembled from available state agency
portals and archived source material. Coverage varies by state and period.

**v1.0.1 data (September 24, 2026):** the checked-in source-only release
contains **71,490 admitted notices, 79,261 versions, and 7,482,177 reported
affected workers**. The database dump, national and state CSVs, and site build
derive from the same pinned agency bundle. The [release notes](docs/release-v1-2026-09-24.md)
explain the material coverage change from the previous main-branch data,
including zero admitted Iowa and Kentucky notices, archive-only collectors,
and other historical gaps. The [source bundle and replay report](data/source_snapshots/README.md)
and [assembly contract](docs/rebuild-contract.md) define the evidence and
rebuild checks. Counts are admitted source events, not an estimate of every
WARN filing nationally.
The [v1.0.1 patch note](docs/release-v1.0.1-cache-fix-2026-09-24.md) explains
the site cache fix; the source data did not change from v1.0.0.

## What this is

The federal WARN Act requires employers to give 60 days' notice of qualifying
plant closings and mass layoffs. Notices are filed with state agencies; there
is no national feed. This pipeline collects available state sources,
normalizes each state's idiosyncratic format into one canonical schema,
deduplicates and version-tracks notices, and commits the results here:

- `data/warn.sql.gz` — the full database as a gzipped SQL dump (notices, versions, run telemetry); `warnlive unpack-db` restores the working sqlite file
- `data/exports/warn_notices.csv` — one row per notice, all active states
- `data/exports/states/{xx}.csv` — per-state cuts
- `data/exports/notice_links.csv` — source-backed relationships, when established; v1.0.1 has no inferred links
- `data/exports/source_observations.csv` — 974 verified Iowa/Louisiana agency observations with admission or exclusion status; the separate exception ledger accounts for other held source rows
- `data/health/health.md` — a dated per-state collection snapshot, not proof that every configured adapter is currently healthy

## Data dictionary (`warn_notices.csv`)

| Column | Meaning |
|---|---|
| `state` | Two-letter postal code |
| `employer_name` | Employer as reported by the state |
| `location` | City/location string as reported (formats vary by state) |
| `notice_date` | Source-supported notice date when its role is known (ISO-8601 or null) |
| `effective_date` | First layoff/closure date (ISO-8601) |
| `employees_affected` | Reported headcount (null when the state omits it) |
| `layoff_type` | `closure`, `mass_layoff`, or `unknown` |
| `is_temporary` | 1 if the state flagged it temporary, else 0/blank |
| `is_amendment` | Source flagged this filing as amending an earlier one |
| `is_amended` | We have observed more than one version of this notice |
| `current_version` | Version count (see `notice_versions` in SQLite for history) |
| `source_url` | The state portal the record came from |
| `source_notice_id` | Agency filing ID when available; otherwise a source-specific identifier or content hash |
| `dedupe_key` | Internal stable record key; source-specific filing identity governs some states, so do not reconstruct it from employer/date/location |
| `first_seen` | When this pipeline first observed the record; historical replay timestamps may describe the capture used in the build |
| `last_seen` | Observation lifecycle marker where tracked; blank does not by itself prove the filing is still listed by the agency |

Additional columns retain filed and derived facts: `normalized_name`,
`canonical_name`, `canonical_basis`, and `employer_key` support employer
navigation; `industry`, `naics`, `naics_basis`, `naics_level`, `sic`, and
`sic_description` describe industry evidence; `cik`, `ticker`, `cik_match`,
`ein`, `ntee`, `lei`, `wikidata_qid`, `wikidata_match`, `parent_company`,
`parent_cik`, and `identity_source` are optional identity annotations, not
proof of ownership at the filing date. `place_name`, `place_fips`,
`county_name`, `county_fips`, `latitude`, `longitude`, and `geo_basis` are
optional resolved geography; `site_address` is the reported worksite address
when supported. `notice_date_precision` and `notice_date_basis` qualify the
legal notice date; `effective_date_precision`, `effective_date_basis`,
`effective_date_end`, `effective_date_end_precision`, and
`effective_date_end_basis` qualify reported action dates and intervals.
`source_identity` and `source_details` preserve source-specific identity and
structured facts. Empty values mean the corresponding fact is not established
in this export.
The [date-precision expansion](docs/date-precision-automation-2026-09-24.md)
describes the automatic source-cell checks and remaining unassessed dates.

Caveats: jurisdictions disagree about what counts as a notice, how amendments
are reported, and how employees are counted. Arkansas, New Hampshire,
West Virginia, and Wyoming have no supported notice-level source in this
register; Puerto Rico is likewise absent. Coverage per jurisdiction starts
at whatever supported source history is available; see
`data/health/status.json` for the collection snapshot.

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
- **Notice relationships**: source-specific repairs can link notices when
  preserved agency evidence establishes their relationship. `warnlive dupes`
  removes legacy links inferred only from name similarity, nearby dates, or
  amendment markers, then exports the remaining source-backed links. An
  ambiguous pair is not linked or merged.
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
warnlive backfill-archives       # historical agency archive sources
warnlive report --gh-issues      # open/close per-state health issues (CI)
```

Scheduled runs commit to `data/`, so **a local session must pull and unpack the
committed database before regenerating anything**:

```bash
git pull && warnlive unpack-db               # before any export/build-site
```

Regenerating exports from a stale local `data/warn.sqlite` silently reverts
whatever CI collected in the meantime — the row counts still look right, because
the loss is of notices you never had. Push promptly for the same reason.

Every scheduled run ends with `warnlive check-regressions`, which compares the
whole database against `data/health/snapshot.json` from the last published run:
notices are only ever added, no state's history shrinks, no single notice covers
100,000 workers, and no field empties out. It fails the run before the commit
and deploy steps, so data that trips it never lands. Per-state checks guard a
scrape against its source; this guards the database against itself.

Scheduled runs: `.github/workflows/scrape-daily.yml` (high-volume states) and
`scrape-weekly.yml` (full sweep, Sundays). Optional secret `ZYTE_API_KEY`
enables the Zyte proxy for states behind aggressive bot protection (LA, TX
fallback, MA fallback).

### Rebuild v1.0.1 from frozen agency sources

The source bundle freezes current state captures, agency archives, and
reviewed original-source artifacts. To reproduce the published 71,490-notice
data with expanded date-precision metadata, use the release-code commit
`b941649` and rebuild into an isolated path:

```bash
python -m warnlive.migrate.source_bundle verify \
  data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.tar.gz
python -m warnlive.migrate.offline_rebuild \
  --bundle data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.tar.gz \
  --db /tmp/warn-v1.sqlite --observed-at 2026-09-24 --source-only \
  --exceptions /tmp/warn-v1.exceptions.jsonl --report /tmp/warn-v1-report.json
```

The replay uses no network or model calls. It accounts for admitted,
coalesced, and excluded source rows; an excluded observation is a conservative
outcome for the available evidence. Compare its counts and content fingerprints
to the [date-precision replay checkpoint](docs/date-precision-automation-2026-09-24.md).
The older [v1 source report](data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1-report.json)
predates that metadata change and has different content fingerprints.

The current development branch also has revision-aware admission rules. Running
the same command with those later rules creates an isolated
[revision-admission candidate](docs/revision-admission-candidate-2026-09-24.md),
whose notice totals differ from the released database; it does not replace the
release artifacts.
The [release notes](docs/release-v1-2026-09-24.md) state the coverage limits
and source-policy changes. `source_observations.csv` contains the verified
Iowa/Louisiana observations; other held source rows are in the exception
ledger. The [assembly contract](docs/rebuild-contract.md) defines the
reconciliation checks. For a dated site build, pass
`warnlive build-site --db /tmp/warn-v1.sqlite --out /tmp/warn-v1-site --as-of 2026-09-24`.

To compare a newer live capture without overwriting an older source bundle,
run `warnlive scrape ca nj --smoke --workdir /path/to/fresh`, then create a
*new* bundle with `source_bundle create --raw-overlay /path/to/fresh/raw` in
addition to the arguments above. The manifest lists each overlaid state CSV.

### Employer identity and industry

Exports carry derived columns the database never stores — identity (SEC
CIK, IRS EIN, LEI, Wikidata QID), industry codes, and the `employer_key`
that groups a company's notices across spelling variants. All of it comes
from reference files under `data/reference`, rebuilt manually and
committed; scheduled runs read them and contact no external service.
`warnlive/enrich/annotate.py` is the single place the tiers combine.

```
warnlive edgar-refresh       # SEC name -> CIK, era-aware (needs SEC_EDGAR_UA)
warnlive edgar-sic-refresh   # SIC industry per matched CIK (needs SEC_EDGAR_UA)
warnlive nonprofit-refresh   # IRS EIN + NTEE code for exempt organizations
warnlive gleif-refresh       # Legal Entity Identifiers for private companies
warnlive subsidiary-refresh  # subsidiary -> parent, from 10-K Exhibit 21
warnlive wikidata-refresh    # Wikidata entities keyed by CIK
warnlive wikidata-labels     # Wikidata for CIK-less employers, exact labels
warnlive places-refresh      # Census place/county roster for locations
```

The SEC commands require `SEC_EDGAR_UA` set to a declared user agent per
SEC fair-access policy, e.g. `SEC_EDGAR_UA="Your Name you@example.com"`.

Every name-based match is gated the same way: equality after
normalization, a corroborating attribute where one exists (filing era for
CIKs, state for EINs), and a single surviving candidate. Ambiguity
matches nothing — a missing identifier costs only enrichment, while a
wrong one silently poisons every join made against it.

WARN forms carry one employer field, so states append the site to the
company: "Ford Motor Co. - Flat Rock", "KMART - STORE #3671", "Aramark
Campus, LLC (University of Kentucky)". A name that fails to match is
retried with that qualifier set aside — recorded as `exact:base` and the
like, since it is a weaker claim than the filed name supports outright.
The filed name is still what gets displayed, and dedupe keys are built
from it, so notices stay distinct even when their employer resolves.

A name that lacks one unambiguous match retains its filed employer name
and leaves the unsupported identifiers blank. The notice itself remains
eligible when its source and event identity are established.

### Where a notice happened

States write locations however they like — a bare city, a city and its
county, a street address, several sites in one field — so `location` on
its own joins to nothing. `warnlive/enrich/places.py` resolves it against
the Census rosters of places, counties and townships, adding
`place_name`, `place_fips`, `county_name`, `county_fips`, `latitude`,
`longitude` and `geo_basis` to every export when the source and Census
roster establish a place. County FIPS codes can join to BLS and Census data.

`places-refresh` rebuilds `data/reference/places.csv.gz` from four Census
files. Three are rosters; the fourth is county boundaries, needed because
a city like Chicago or Atlanta straddles county lines and the roster
cannot say which county it belongs to — the place's own interior point
decides, unless it shares its name with one of its counties, in which
case that is the one it is named for.

The matching rule is the identity rule: exact after normalization, one
survivor, and never across a state line. A county the state filed in its
own field settles a city name that repeats — but a single segment
matching both a place and a county is a coincidence, not a filed county,
which is why Houston resolves to Harris County rather than to the Houston
County it is not in.

States also file out-of-state addresses — a corporate headquarters rather
than the worksite — and share city names with the states they file into.
A location that names its own state, and names a different one, resolves
to nothing: reading "2323 KENNEDY DRIVE JANESVILLE, WI 53547" against
Illinois would place the layoff in the Janesville Illinois has, which is a
wrong answer given confidently rather than a missing one.

Locations that the Census roster and source fields cannot place retain
the filed `location` text and leave derived place and county fields blank.
Unknown geography does not exclude a source-identifiable notice. Historical
model experiments remain outside the public repository and are not loaded by
the pipeline.
