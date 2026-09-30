# WARN Notice Register v1.3.0: recovered agency history

v1.3.0 contains **85,810 admitted notices** (100,268 versions), reporting
**8,844,698 affected workers**. It has two parts:

- **A full replay** of a new dated
  [source bundle](../data/source_snapshots/2026-09-30-v1.3-source-bundle.tar.gz):
  85,688 notices, 100,136 versions, 8,830,761 workers. That is 4,985
  notices and 625,572 workers more than v1.2.0 (80,703 and 8,205,189).
- **One dated live scrape** of the daily states on 2026-09-30, run on the
  replay exactly as the scheduled workflow runs it. It adds the
  122 notices those agencies listed after the bundle's frozen captures (the
  same notices the 2026-09-30 scheduled scrape added to v1.2.0, plus
  3 listed later that day).

The bundle is the v1.2 bundle plus pinned official agency files for twelve
states, recovered from agency sites and the Internet Archive's copies of
agency pages. Nothing comes from a third-party dataset except Oregon's
historical workbook (see known limits). The replay leaves every v1.2.0
notice unchanged; the live run then updates them exactly as the 2026-09-30
scheduled scrape did on v1.2.0 (nine Illinois and New York notices gain a
version, and notices no longer on a complete current listing get `last_seen`).
The [recovery record](source-recovery-2026-09-30.md) has the exact commands,
hashes, per-source tables and checks. Counts are admitted source entries, not
an estimate of every WARN filing nationally.

## What changed for users of the data

### Recovered history

| State | Source | Years | Notices added | Workers added |
|---|---|---|---:|---:|
| Kentucky | Kentucky Career Center tracking workbook (1998–2016), report workbook (2017–Feb 2025) and 2025 report CSV | 1998–2025 | 1,150 | 136,790 |
| Tennessee | Archived "WARN Summary by Month" report (2012–2017) and reports-page captures (2017–2021) | 2012–2021 | 869 | 96,402 |
| Michigan | Archived michigan.gov WARN year and main pages | 2014–Jan 2022 | 583 | 100,773 |
| Louisiana | Archived annual WARN tables | 2007–2024 | 544 | 72,324 |
| Arizona | Arizona Job Connection WARN listings (full portal capture) | 2010–2026 | 531 | 82,704 |
| Connecticut | Archived CT DOL annual listing pages (2010–2012, 2014–2025) | 2009–2025 | 490 | 45,316 |
| Iowa | Archived IWD WARN logs (2005–2018 notices) | 2005–2018 | 377 (+61 amendment versions) | 39,662 |
| Ohio | Archived ODJFS 2023 and 2024 annual pages and current-year pages | 2023–2025 | 236 | 27,870 |
| Maine | Maine JobLink WARN listings | 2012–2026 | 68 | 7,407 |
| Delaware | Delaware JobLink WARN listings | 2008–2026 | 44 | 8,579 |
| Vermont | Vermont JobLink WARN listings | 2003–2025 | 48 | 4,491 |
| Oregon | Historical workbook rows missing only a layoff date or worker count | 1993–2020 | 45 | 3,254 |

Years are the notice year where the source gives one, else the agency's
received or posting year. Workers are the sum of reported counts; 230 of the
new notices report none.

- **Identity.** A notice number, WARN number or notice document ID is the
  identity where the source gives one; otherwise the table row. A row that may
  repeat a notice already in the register is held, never merged.
- **Dates keep their roles.** Kentucky's `Date Received`, Connecticut's
  "Rec'd" and Ohio's "Date Received" are `agency_received_date`; Tennessee
  and Michigan posting days are `agency_posted_date` (Michigan notices have
  no canonical date). Kentucky's projected dates are the effective date, and
  ranges keep both ends.
- **Implausible dates are blanked, not guessed.** Five parsed dates fall
  outside the window the live scrape applies (below the state's minimum
  year, or more than a year after the source was captured). They are blank
  and the source cell is kept: Kentucky Cenveo's projected date 2041-06-04
  (received 2014-04-04), Iowa Gleason's layoff date 1969-10-20 (notice
  2006-08-18), and two Connecticut range ends on the 2012 page, which was
  captured 2012-12-20 (Northrop Grumman to 2014-12-26, Océ to 2013-12-31).
  The Kentucky projected date also leaves `source_details`, so the Cenveo
  notice counts one blanked value there and one in `effective_date`.
- **Job portals.** Arizona, Maine, Vermont and Delaware are now collected
  from their job portals' WARN search by a shared collector, as Kansas is.
  Released notices stay on their July rows; the new capture adds only
  records not already in the register.
- **National CSV is now gzipped.** At 105.6 MB, `warn_notices.csv` outgrew
  GitHub's 100 MB file limit, so the repository now carries
  `data/exports/warn_notices.csv.gz` (13.3 MB; same rows and columns,
  written with a zeroed gzip timestamp so unchanged data gives unchanged
  bytes). The per-state CSVs are unchanged.

### Held rather than admitted

The [exception ledger](../data/source_snapshots/2026-09-30-v1.3-candidate.exceptions.jsonl.gz)
has 7,972 rows (v1.2.0: 5,702). Rows held from the new sources, by reason:

| State | Source rows | Held | Main reasons |
|---|---:|---:|---|
| Kentucky | 1,227 | 77 | amendment parent unresolved 33, non-WARN tracking row 12, in the 2025 report with a number 11, out of state 10, re-entered row 6, already admitted 4, rescinded 1 |
| Tennessee | 1,178 | 309 | earlier capture of a WARN number 256, number lists different events 25, already admitted 26, report row also on the page 2 |
| Michigan | 938 | 355 | another capture's listing of the same document 342, update listing 12, not a notice link 1 |
| Louisiana | 587 | 43 | update unresolved 25, continuation row 11, rescinded 7 |
| Connecticut | 939 | 449 | update or revision row 446, rescinded 1, continuation 1, duplicate 1 |
| Iowa | 1,054 | 616 | duplicate capture 361, in the current logs 132, amendment without verified parent 64, amendment parent ambiguous 37, possible revision 6, differs in newer log 5, layout or date 5, site or worker allocation 4, repeated event 2 |
| Ohio | 351 | 115 | duplicate capture 90, conflicting notice ID 13, update-only listing 4, invalid ID 4, superseding amendment 3, already admitted 1 |
| Arizona | 767 | 236 | in the July capture 208, same employer and day 26, name variant 2 |
| Vermont | 101 | 53 | in the July capture 51, same employer and day 2 |
| Maine | 94 | 26 | in the July capture 9, same employer and day 17 |
| Delaware | 80 | 36 | in the July capture 32, same employer and day 4 |
| Oregon historical | 1,082 | 103 | newer capture overlap 80, place-only employer 16, placeholder or missing number 2, multi-site identity 2, same employer and received day 2, duplicate capture 1 |

Portal records held at staging (same employer and day, name variants) are
not ledger rows; the portal evidence archives keep them.

## Reconciliation and checks

- **Released notices.** In the replay, every one of the 80,703 v1.2.0
  notices is present with an identical row and identical versions, and no
  link was lost. The live run added no notice beyond the 119 of the
  2026-09-30 scheduled scrape and three listed later that day, and removed
  none.
- **Row accounting.** Every source row of each new source is admitted,
  versioned, or in the ledger with its reason.
- **Determinism.** Two independent replays have identical fingerprints and
  byte-identical ledgers. The v1.2 bundle under this code still reproduces
  the v1.2.0 report and ledger.
- **Live run.** All 13 daily states completed; the regression gate, the
  publication gate and the run-report check passed.
- **Consistency.** The national CSV equals the sum of the state CSVs and the
  database.
- **Tests.** The full Python suite, the site tests and the site build pass.
- **Independent review.** A review recomputed row accounting per source,
  compared every released key, searched the new notices for repeated events
  and traced sampled rows to source cells. Its findings (Iowa repeats under
  retyped names, Kentucky tracking-sheet re-entries, live/replay parity for
  the portal holds) are fixed in this release. The date window was added
  after that review, when release assembly found the Kentucky 2041 date.

Rebuild the replay with:

```bash
python -m warnlive.migrate.offline_rebuild \
  --bundle data/source_snapshots/2026-09-30-v1.3-source-bundle.tar.gz \
  --quality-evidence-dir data/source_snapshots/2026-09-24-quality-evidence \
  --db /tmp/warn-v1.3.sqlite --observed-at 2026-09-30 --source-only \
  --or-historical-partial-rows \
  --report /tmp/warn-v1.3-report.json --exceptions /tmp/warn-v1.3.exceptions.jsonl
```

The published database is that replay plus the dated live run recorded in
the [release manifest](../data/source_snapshots/2026-09-30-v1.3.0-release-manifest.json).

## Known limits

- **Gaps that remain.**
  - Georgia before 2018: no agency source found.
  - Michigan February 2022 through 2023: no archived agency listing.
  - Missouri 2015–2018: not recovered.
  - About 580 Illinois rows need agency records requests.
  - Tennessee before 2012, Connecticut 2005–2009 and 2013, and Louisiana
    after August 2024 (archive) have no usable capture; captures of rolling
    pages miss notices listed only between captures.
- **Oregon's historical workbook is secondary-hosted.** The 783 historical
  Oregon notices in v1.2.0 and the 45 partial rows added here come from an
  exact copy of an agency-supplied workbook hosted on Big Local News's
  GitHub, not a file fetched from an Oregon site. All 45 partial rows lack a
  layoff date; 32 also lack a worker count.
- **Unverified date roles.** Tennessee's month report `Notice Date` and
  Connecticut's `WARN Date` fill `notice_date` with no basis; in the
  Tennessee report 313 of 509 admitted rows have a notice date after the
  received date. Leave these rows out of strict notice-timing work until the
  roles are verified.
- **Released Iowa repeats.** 18 pairs of v1.2.0 Iowa notices repeat one
  event under a typo or truncated name (the current event log and the 2023
  PDF). Correcting them changes released keys and awaits a reviewed
  migration.
- **Hawaii is paused.** Its collector's new output re-keys every released
  row; scheduled collection resumes after a key migration.
- **Held rather than guessed.** Kentucky amendment rows, Tennessee numbers
  the agency reused, Louisiana "Update:" stacks, Connecticut update rows and
  Michigan update listings are held without linking to their originals.
- **Rolling sources and freshness.** Scheduled scrapes add newer notices
  after publication. Massachusetts runs may use Internet Archive copies of
  mass.gov files and are then marked degraded.
- **No supported notice-level source:** Arkansas, New Hampshire, Wyoming and
  Puerto Rico; West Virginia's collector is unverified.
