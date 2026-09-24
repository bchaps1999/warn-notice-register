# Texas WARN date evidence: source audit before promotion

The v5 source bundle's `raw/tx.csv` has 7,572 rows (SHA-256
`9c3989392d65a241cf8acd66cfacb2a9e1eb8412242c24acdfe1d5c5217a454c`).
The active staged database has 7,319 Texas notices after existing
normalization and deduplication. The installed BLN transformer maps the raw
`NOTICE_DATE` field to canonical `notice_date` and `LayOff_Date` to canonical
`effective_date`. It does not use `WFDD_RECEIVED_DATE` as a notice date.
The [Texas Workforce Commission WARN listing](https://www.twc.texas.gov/data-reports/warn-notice)
publishes annual workbooks. These fields describe the agency's listed WARN
notice and anticipated layoff dates, not proven individual employee receipt or
actual separation dates. The [TWC/WIOA annual narrative](https://www.dol.gov/sites/dolgov/files/ETA/Performance/pdfs/PY2024/Annual/narratives/TX_PY24%20WIOA%20Annual%20Narrative%20Report.pdf)
uses distinct received-notice and anticipated-layoff language in an example.

## Source lineage and raw-cell checks

The local cached 2020–2026 annual workbooks each use the eight named columns
`NOTICE_DATE`, `JOB_SITE_NAME`, `COUNTY_NAME`, `WDA_NAME`,
`TOTAL_LAYOFF_NUMBER`, `LayOff_Date`, `WFDD_RECEIVED_DATE`, and `CITY_NAME`.
Every one of their 2,475 nonempty row occurrences has an exact eight-field
counterpart in the pinned raw CSV (2020: 1,209; 2021: 123; 2022: 86; 2023:
225; 2024: 464; 2025: 289; 2026: 79). The 2024 workbook republishes 225
2023-dated rows; workbook year must not be interpreted as notice year. The
2026 cached workbook has 79 rows, three more than the older local
`2026-twc.xlsx` copy. The v5 bundle pins the projected CSV but does not include
the underlying workbooks. A new [v6 source bundle](../data/source_snapshots/2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz)
(SHA-256 `4f9ccb698ea9d035514bb2c18d65d3d39ad66a7a9001691544580379ed9dc752`)
pins the seven annual workbook versions, source-page snapshot, manifest,
original URLs, and row/header counts. The 2026 version in that bundle is the
79-row workbook matching the frozen raw corpus.

All 7,572 raw `NOTICE_DATE` cells parse as days; 7,558 `LayOff_Date` cells
parse and 14 are blank. There is no date-range syntax in these two fields.
Among 7,558 raw pairs, 1,177 have a notice day after the listed layoff day.
These negative intervals require a separately reported review stratum; a
negative gap alone does not prove a parser error or justify changing the raw
dates. The transformer also corrects four unusual raw layoff years (two 1930
cells and two 2027 cells) to other years. Those four cannot receive
`reported` basis merely because the corrected canonical day is valid;
reviewed correction provenance is needed.

## Historical receipt-column defect

The pre-2019 source is a BLN-hosted historical workbook rather than the TWC
annual workbook corpus. In its header, `NOTICE_ID` is column 12 (zero-based
index 11) and `WFDD_RECEIVED_DATE` is column 13 (index 12). Both the pinned
upstream scraper and the local patched scraper had copied index 11 into the
raw CSV under `WFDD_RECEIVED_DATE`; a sample Philips Electronics row therefore
carried numeric ID `2299` where the workbook has a January 4, 1999 receipt
date. The local [Texas fetch patch](../warnlive/fetch/patches/tx.py) now
resolves historical columns by name and fails closed if one is missing or
repeated. Its two targeted tests pass. The frozen v5 candidate and bundle
remain unchanged; the corrected scraper affects future captures, which must
be compared and pinned as a new source version.

## Next decision

The new `tx_annual_workbook_dates_v1` implementation requires workbook/raw
eight-field correspondence and one unambiguous current candidate match.
It holds a canonical key if previous versions contain distinct raw rows. One
observed example is Willie's Grill & Icehouse in San Antonio: the old key
contains two April 10, 2020 notice / March 17 layoff rows with 32 and 45
workers. The version history does not establish whether they are a revision
or separate events, so this key receives no source-backed timing metadata.
It retains workbook hash, sheet/row, source cells, date roles, and rule version.
It does not use `WFDD_RECEIVED_DATE` as legal notice, does not invent an
effective end, and holds anomalous 1930/2027 effective-year cells. The worker's
initial isolated smoke check, before the same-key guard, annotated 2,244 Texas
notices with workbook-backed notice dates and 2,235 with workbook-backed
anticipated layoff dates; 813 matched unique source rows had negative intervals
flagged rather than silently changed. These were smoke counts before the
same-key guard. BLN historical Texas rows remain unassessed until their lineage
and date roles are separately reviewed.

The final guarded v6 replay matches across the active and fresh hash-locked
Python environments. The rule reports 2,475 workbook row occurrences, 2,245
unique source rows, 2,243 matched current notices, 2,243 source-backed notice
dates, and 2,234 source-backed anticipated layoff dates. It holds two source
rows and three corrected effective dates; one anomalous raw-year row lies
outside this annual corpus. It flags 812 negative source intervals. The
strict timing cohort therefore gains 2,234 Texas starts but no Texas range
ends. The [v6 state summary](date-eligibility-state-summary-2026-09-23-v6.md)
and [progress log](rebuild-progress.md) record the combined checkpoint.
