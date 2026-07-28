# WARN Notice Register

A consolidated, normalized, deduplicated dataset of WARN Act layoff notices,
refreshed automatically from ~47 state portals.

## What this is

The federal WARN Act requires employers to give 60 days' notice of qualifying
plant closings and mass layoffs. Notices are filed with state agencies; there
is no national feed. This pipeline scrapes every state that publishes online,
normalizes each state's idiosyncratic format into one canonical schema,
deduplicates and version-tracks notices, and commits the results here:

- `data/warn.sql.gz` — the full database as a gzipped SQL dump (notices, versions, run telemetry); `warnlive unpack-db` restores the working sqlite file
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

A refusal is not always an absence, though: often a rule saw a plausible
registrant and lacked a tiebreaker. `warnlive identity-review` writes
those near-misses to `data/health/identity_review.csv` — the candidate,
its filing era, the gate that rejected it, and how many workers ride on
the answer — for a human or a model to adjudicate from the same facts.

Decisions come back in `data/reference/identity_overrides.csv`:

| Column | Meaning |
|---|---|
| `normalized_name` | The employer name as normalized (the review file's first column) |
| `cik` / `ein` / `lei` / `wikidata_qid` | Whichever identifiers were decided |
| `decided_by` | Who or what decided (a person, a model, a ticket) |
| `decided_at` | When |
| `note` | Why — the evidence that settled it |

### Where a notice happened

States write locations however they like — a bare city, a city and its
county, a street address, several sites in one field — so `location` on
its own joins to nothing. `warnlive/enrich/places.py` resolves it against
the Census rosters of places, counties and townships, adding
`place_name`, `place_fips`, `county_name`, `county_fips`, `latitude`,
`longitude` and `geo_basis` to every export. 79% of notices reach a
county FIPS code, which is the key that joins to BLS and Census data.

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

Local knowledge that no roster carries goes in
`data/reference/place_aliases.csv` — NYC boroughs, Los Angeles
neighbourhoods, abbreviations states use. A `kind` of `county` says the
alias can only be placed at county level, as for an unincorporated
community that is in no city at all. An alias may name a whole filed
string rather than a name inside it, which is the only way to place
something like "O'HARE INTERNATIONAL AIRPORT CHICAGO, IL 60666", where no
segment is a place and no rule will make one. A `decision` of `reject`
records that a string names no geography at all, so it stops returning to
the review file; it grants nothing.

`places-refresh` also writes `data/health/places_review.csv`, every
unresolved location ranked by workers at stake; much of the top of that
file is Kansas, Vermont, Maine and Oklahoma filing against workforce
investment areas, which are not places and never resolve.

Overrides outrank every automatic tier and are reported as
`identity_source=override` in exports, so an adjudication can always be
audited or revoked. Nothing in the pipeline writes these files by itself;
no adjudication is ever inferred.

### Adjudication

Each of those review files is a queue of things no rule can settle,
because settling them needs knowledge the corpus does not contain. `warnlive
adjudicate` works those queues with a model, and every proposal is judged by
the same code that refused in the first place:

```bash
warnlive adjudicate places               # unresolved locations
warnlive adjudicate identity             # unidentified employers
warnlive adjudicate industry --calibrate # measure before trusting
warnlive adjudicate industry             # then assign sectors
```

The model proposes; it never rewrites. A location alias is written into the
table and the resolver is run again on the original string — either it now
names a real Census place or the proposal is worth nothing, so an invented
city fails on the gazetteer rather than on anything the prompt said. A
proposed registrant must clear the unmodified EDGAR matcher and then be
corroborated at least twice by evidence the proposal never saw: the filing
calendar, a parent's Exhibit 21, the state-published industry, the IRS or
GLEIF rosters. Anything unproven is staged under `data/health/*_adjudicated.csv`
for a person instead of being written.

A subsidiary becomes a parent link in
`data/reference/subsidiary_overrides.csv`, never an identity: First Transit
is owned by FirstGroup and is not FirstGroup, and writing the parent's CIK
into its identity would conflate the two in every join made afterwards. For
the same reason an employer that its own proposed registrant lists in
Exhibit 21 is refused — a company does not appear in its own subsidiary
schedule.

Industry is the one queue with no authority to check an answer against, so
its threshold is measured rather than chosen. `--calibrate` classifies the
employers whose industry a state already published, with the label hidden,
and writes precision at each confidence cut to
`data/health/industry_calibration.csv`. Scoring is per employer, never per
notice. Adjudicated sectors are reported as `naics_basis=adjudicated`,
ranked below every basis tracing back to an authority.

Every question and answer is appended to
`data/reference/adjudications.jsonl.gz`, keyed by task, row, prompt version
and model. A rerun replays it and calls nothing; `--dry-run` re-judges those
stored answers through today's gates, which is how a change to a gate is
checked before any money is spent. Refusals are recorded too — that is what
stops an unanswerable row returning on every refresh.

This runs by hand and never in CI: scheduled scrapes read reference files
and contact no model. The provider is a base URL and a key name in
`warnlive/adjudicate/providers.yaml` (DeepSeek by default, needs
`DEEPSEEK_API_KEY`), so changing models is a flag and changing vendors is a
config edit. `--budget` caps spending and is checked before each call; a
model with no prices on file reports tokens and an unknown cost rather than
a confidently wrong one.
