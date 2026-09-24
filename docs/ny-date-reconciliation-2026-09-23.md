# New York date reconciliation checkpoint

Audit date: 2026-09-23. This extends the [candidate date audit](date-candidate-audit-2026-09-23.md).
It uses the isolated date-fixed candidate at
`/private/tmp/warn-date-fixed-20260923-v2.sqlite` (SHA-256
`8719fb03736b62684c916e8f7e127641d45519a020293cefef12257a04f4ef07`).
The ledger uses the pre-repair candidate; a separate reviewed source-only
rebuild now applies one filing-verified correction described below.

## Evidence frozen

The local 2022–24 [New York WARN dashboard](https://dol.ny.gov/warn-dashboard)
CSV caches are pinned under
`data/source_snapshots/ny/` with their original bytes, source URLs, file
hashes, row counts, and duplicate worker-count columns. They contain 132, 285,
and 341 data rows respectively, for **758 source rows**. The manifest labels
the old cache file mtimes as filesystem mtimes; these are **not confirmed
download times**. The dashboard has separate `Date of WARN Notice`, `Date
Posted`, and `Date Layoff/Closure Starts` columns. It has no end-date column.
`Index` repeats within each year and is not a notice identity.
The first reviewed bundle pins one New York filing PDF and one explicit
decision file. The later First Transit bundle pins a second filing and decision.
Both filings supply closure end dates absent from the dashboard.

`data/source_snapshots/2026-09-23-ia-la-ny-dates.tar.gz` (SHA-256
`76f5013078bf5d40a0e9f5e2384ad3cde1e3444bf57e810e12f4b0255fe6cece`)
adds exactly four `agency/ny/` files to the earlier Iowa/Louisiana bundle;
all 1,986 prior files retain their exact hashes and sizes. The new bundle
passes `source_bundle verify`. The source-only rebuild does not yet apply
these NY dashboard rows to active notices.
An isolated replay with the new bundle retained the earlier **86,388 notices,
92,492 versions, 9,493 links, 8,998,760 workers**, and identical active
content fingerprints and state counts; SQLite integrity is `ok`. The
exception-ledger checksum changed because four held Iowa PDF rows were parsed
differently by concurrent Iowa extractor edits during the two runs. This is
not an NY admission effect and is not evidence of identical full replay
outputs under one fixed code revision.

## Review ledger

Run:

```bash
python -m warnlive.migrate.ny_reconcile \
  --bln workdir/backfill/bln_integrated.csv \
  --ny-artifacts data/source_snapshots/ny \
  --candidate-db /private/tmp/warn-date-fixed-20260923-v2.sqlite \
  --out-dir data/review/ny-dates/2026-09-23
```

The [summary](../data/review/ny-dates/2026-09-23/summary.json) binds the
ledger to the BLN file, NY source manifest, and candidate database hashes.
The [BLN ledger](../data/review/ny-dates/2026-09-23/ny_bln_review.csv) has
all **928** NY BLN source rows dated 2022–24; **780** are in the candidate and
**148** are marked superseded. The [official ledger](../data/review/ny-dates/2026-09-23/ny_official_review.csv)
has all 758 dashboard rows. The [pair table](../data/review/ny-dates/2026-09-23/ny_same_name_source_pairs.csv)
contains **933** exact-normalized-name source pairs with three explicit date
comparisons. These are review nominations, not accepted correspondences.
Regenerating from the extracted NY-augmented source bundle yielded identical
CSV hashes.

| BLN review category | Rows |
| --- | ---: |
| One dashboard row also matches the BLN date in at least one named role | 85 |
| More than one dashboard row matches the BLN date in a named role | 24 |
| Name matches, but no named date role matches | 251 |
| No exact normalized-name match | 420 |
| Superseded BLN source row | 148 |

Among the 85 one-row date-role nominations, **64** match `Date Posted` alone,
13 match both notice and posted, three match notice alone, three match layoff
start alone, one matches notice and layoff start, and one matches posted and
layoff start. The posted-only group is evidence of a systematic date-role
problem to inspect, not a count of 64 proven errors. The BLN rows often lack
site, worker count, and a filing control number, so a unique name/date
nomination can still confuse separate sites or amendments.

The Sodexo/SUNY Polytechnic filing illustrates both the role issue and
duplicate identity problem. The official dashboard row reports notice
**2022-06-21** and layoff start **2022-06-30**, consistent with the
[agency PDF](https://dol.ny.gov/system/files/documents/2022/06/warn-sodexo-suny-polytechnic-mohawk-valley-2021-0088-6-30-2022.pdf).
The pre-decision candidate contained one BLN row dated June 21 and another
dated June 30, both without an effective start. The reviewed decision below
establishes one filing event, excludes the latter duplicate observation, and
repairs the retained row's date roles without asserting an exact employee
separation date.
For 3E Logistics, the official dashboard row reports notice September 16 and
both posting and layoff start September 30, while the pre-repair candidate's
BLN `notice_date` is September 30. The [agency filing](https://dol.ny.gov/system/files/documents/2024/09/warn-nyc-3e-logistics-nj-inc.-9.16.24-2024-0066.pdf)
identifies event 2024-0066, the same employer and Brooklyn site, 84 affected
workers, `Date of Notice` September 16, and both `Closure Start Date` and
`Closure End Date` September 30. This supports a 14-day notice-to-start interval.

## Reviewed repair

The first decision in `data/source_snapshots/ny/reviewed_date_repairs.json` pins the
BLN source ID and raw-row hash, the official dashboard row ID and hash, the
filing PDF hash, the old candidate values, and the selected dates. The
source-only rebuild checks these values and key collisions before editing,
retains the existing notice ID and raw BLN record, and appends a version with
provenance. The first reviewed bundle applies only the 3E Logistics correction: notice **2024-09-16**,
effective start **2024-09-30**, effective end **2024-09-30**. It does not infer
an end from the dashboard or update other NY records by name.

The reviewed bundle `data/source_snapshots/2026-09-23-ia-la-ny-reviewed.tar.gz`
contains 1,992 files and verifies with SHA-256
`814ef569cadc703ae9553d22e7bd5e13538946cf87212dc90143135c3ed7f8bc`.
Relative to the earlier NY bundle, only `agency/ny/manifest.json` changed;
the filing and decision file were added. All other bundled input hashes match.
Recreating the reviewed bundle from the same frozen inputs yielded the same
SHA-256. The isolated source-only replay reported **86,388 notices, 92,493
versions, 9,493 links, and 8,998,760 workers**, with SQLite integrity `ok`
and no foreign-key errors. The repair report lists exactly the pinned 3E BLN
source ID as applied, with no key collision. Comparing all NY records by BLN
source ID against the earlier NY evidence candidate found exactly one changed
NY notice: 3E's notice/start/end dates, dedupe key, provenance, and version.
The read-only timing gate rises from **55,998** to **55,999** provisional
day-granular paired rows and includes 3E at **14 days**; it does not validate
the rest of that cohort.
Two full source-only replays with the reviewed bundle reported identical counts,
repair decisions, and stable-content fingerprints for notices, versions, and
links. SQLite file checksums differ because those bytes include operational
details excluded from the content fingerprints.

## Second reviewed filing: First Transit

The local NY artifact directory now pins the [agency filing for First Transit,
event 2023-0298](https://dol.ny.gov/system/files/documents/2024/03/warn-first-transit-transdev-north-america-western-2023-0298-3-27-2024.pdf)
as `filings/first-transit-2023-0298.pdf` (SHA-256
`f54f17da946d75673e48a837d537d666baf5c0e92616a91618cc4cfbe7dab46e`).
Its employer, 2700 Millersport Hwy in Getzville, Erie County, and **65 affected
workers** agree with dashboard row 101 of the pinned 2024 CSV. The filing labels
**March 21, 2024** as `Date of Notice` and **June 30, 2024** as both `Closure
Start Date` and `Closure End Date`. The dashboard independently gives March 21
as notice date, March 22 as posting date, and June 30 as layoff start. The BLN
candidate (`source_notice_id=374c4eb61056e8e785b6e49092109e88a1a2c9f48d1a85210d6dc677`)
instead has `notice_date=2024-03-27` and no effective date. March 27 also appears
in the filing's filename and PDF title; it is not the labeled notice or posting
date. This is a **101-day** notice-to-closure-start interval.
The BLN row omits the site and worker count; the correspondence rests on its
exact employer name (unique among these 758 dashboard rows), its March 27
stored date matching the PDF title/filename, and the dashboard and filing's
matching site and worker count. It is therefore an explicit reviewed pair,
not an automatic name-only rule.

The second decision in `reviewed_date_repairs.json` pins that BLN source ID and
raw-row hash, its old dedupe key and date values, dashboard row ID and hash, and
the filing hash. The selected notice/start/end dates are March 21/June 30/June
30. It retains the candidate notice ID and raw source record and adds a version
with provenance when replayed. A read-only check against the isolated reviewed
candidate found no existing notice with the proposed dedupe key
`cf818de78c60b274c19cdfaac9a08b8617340a3e`. The filing's subsidiary
wording is identity evidence, not a date-repair rule for other First Transit
records.

The second bundle,
`data/source_snapshots/2026-09-23-ia-la-ny-first-transit-reviewed.tar.gz`,
contains 1,993 files and verifies at SHA-256
`95d242fcb2cb6485afb6f9cb0da2625fffc7607565c3a372e8e5059642cdeba5`.
Relative to the first reviewed bundle, exactly three members changed: the
First Transit filing was added, and the NY manifest and reviewed decision file
were updated. An isolated source-only replay returned 86,388 notices, 92,494
versions, 9,493 links, and 8,998,760 workers, with integrity `ok` and zero
foreign-key errors. Both reviewed decisions applied without key collisions.
A row comparison against the first reviewed candidate, excluding operational
observation timestamps, found exactly one changed semantic notice: First
Transit. Its old March 27 date and blank effective dates became March 21 and
June 30–June 30; its source ID and raw record were retained, and it gained a
new version. The newer date-metadata replay marks each of its three dates as
`day`/`reported` and the read-only timing gate places the 101-day interval in
both strict start and end cohorts. The earlier reviewed bundle and figures in
the preceding section remain the 3E-only checkpoint.

## Reviewed Sodexo duplicate and date roles

The official [Sodexo filing for event 2021-0088](https://dol.ny.gov/system/files/documents/2022/06/warn-sodexo-suny-polytechnic-mohawk-valley-2021-0088-6-30-2022.pdf)
identifies one dining-services site at **100 Seymour Rd, Utica**, **63 affected
workers**, **June 21, 2022** as `Date of Notice`, and **June 30, 2022** as
`Closing Date`. It states that frontline separations would occur *on or about*
June 30 and management separations on July 28. Its labeled dates were checked
against the rendered page, not inferred from the filename. The live PDF URL
returned HTTP 403 during this review; the bytes pinned locally came from an
[Internet Archive capture of that URL](https://web.archive.org/web/20240000000000id_/https://dol.ny.gov/system/files/documents/2022/06/warn-sodexo-suny-polytechnic-mohawk-valley-2021-0088-6-30-2022.pdf).
The pinned PDF is 79,613 bytes, SHA-256
`eaa6c49739623c3cae0d2a898357e81eb3a7b27dda1e5446f5bfb038f5de52a2`.
Dashboard 2022 row 105 independently reports June 21 notice and June 30
layoff/closure start; its row SHA-256 is
`e6c7d12f20c68712c0c4ae975d68fb587a9b5d646f053f9f3b7af2fc8049a077`.

Two BLN observations point to that one event. The retained regional-name row
has source ID `19b795dfd8a389240fc80414f3ee22c39fe5826bad2a93a05d301d37`,
stored June 21 notice, and no effective date. The shorter-name row has source
ID `6ed4f110cc192819e1964612865026b9bacd5d0022f24ffb19b7fbfc`,
stored June 30 in `notice_date`, and no effective date. Neither BLN row reports
a worker count. The reviewed decision pins both raw-row hashes, source row
ordinals, old keys and dates, the dashboard row, and the filing bytes. Replay
excludes the shorter-name duplicate **before BLN ingestion**, writes an
exception with the retained source ID and review decision pointer, then repairs
the retained row. It fails closed if any pinned source row, key, field, official
row, or PDF changes, or if the duplicate was already admitted.

The retained canonical row has `notice_date=2022-06-21`,
`effective_date=2022-06-30`, and `effective_date_end=NULL`, with day/reported
evidence on the two known endpoints. In this record `effective_date` means the
reported **closing and action start**; the nine-day difference measures notice
to that action, **not exact employee separation timing**. The frontline
approximation and July 28 management date stay as separate phases in
`source_details`; they do not form a continuous end date. The dashboard's 63
workers are retained as source evidence, not promoted into the blank BLN
canonical worker count. The decision and source PDF are in the 1,995-file
`2026-09-23-ia-la-ny-sodexo-reviewed.tar.gz` bundle (SHA-256
`a4c7756fe0ce6682703930988376ed35ad30c128f5366bc650c33354326fd858`).
Relative to the First Transit bundle, only the NY manifest changed and two
new NY files were added; all other source-member hashes are identical.
Focused replay and drift tests passed. The full candidate replay and its
cross-output accounting are being run separately.

## Remaining review boundary

The Arc Greater Hudson
Valley and SpencerARL involve amendments, sites, and phased separations;
their range interpretation is held. Further reviewed decisions need a filing,
control number, site and amendment history, plus pinned source identities and
guarded old values. The 64 posted-only nominations remain excluded, not a
proven error count. The dashboard CSVs supply no interval end; an end stays
blank unless a filing supplies it.
