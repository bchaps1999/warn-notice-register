# Texas and Oregon historical agency workbook expansion

This staged source-only candidate extends the [New York and Georgia candidate](agency-original-source-expansion-2026-09-24.md). Texas and Oregon
workbooks were supplied by the respective agencies to Stanford Big Local News
and remain available from its public [Texas](https://warn-scraper.readthedocs.io/en/latest/scrapers/tx.html)
and [Oregon](https://warn-scraper.readthedocs.io/en/latest/scrapers/or.html)
source notes. Each downloaded file was byte-identical to the corresponding
previously cached workbook. The exact files and custody manifests are pinned at
[Texas](../data/source_snapshots/tx/historical-1989-2019/manifest.json) and
[Oregon](../data/source_snapshots/or/historical-1980-2021/manifest.json).
The raw workbooks, rather than BLN-normalized CSV rows, are the admission
inputs.

| Workbook | SHA-256 | Source rows | Projected | Held | Identity and date rule |
| --- | --- | ---: | ---: | ---: | --- |
| Texas | `0ee280d92a7091d281b6bf8e43a84f38c27bebda3a493545d1cc22a9a799cfc8` | 5,097 | 5,086 | 11 | Unique TWC `NOTICE_ID`; workbook `NOTICE_DATE` is reported notice, `LayOff_Date` is reported action, `WFDD_RECEIVED_DATE` is agency receipt. Ten rows lack a usable city/county; one has an anomalous 2027 action year. |
| Oregon | `86dd5835b4a76906b906435c422fc67b88a24f45e5c795697cf5c6bba892a131` | 1,082 | 723 | 359 | Unique `WARN#` outside all newer agency captures; `Received Date` is agency receipt and `Layoff Date` is reported action. Holds: 80 newer-capture overlaps, 214 repeated IDs, 58 incomplete rows, six rows with an unresolved employer, and one blank ID. |

The Texas file's name says 1989–2019, but its observed notice dates start in
**1999**. The Oregon banner says 1980–2021, but its observed receipt dates
start in **1988**. Texas rows with a numbered notice and a missing worker count
(24) or action date (8) retain those fields as null. A worker threshold is not
used to infer whether a numbered agency record is a WARN notice. Every
source row is admitted or recorded with a reason in the exception ledger.
The incomplete Oregon rows include 24 workbook action-date cells containing
Excel's `1899-12-29` empty-date sentinel. Six more rows have only a street or
city in the employer cell, so they are held until source notices establish the
employer identity.

## Isolated candidate replay

The [combined source bundle](../data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-v1.tar.gz)
contains 1,998 source files and has SHA-256
`165aecaf766c9aa285d7649015284bd1707459f659dc99a83f5a69d8620d4833`.
The corrected replay contains **70,324 notices**, **78,095 versions**, and
**7,311,111 affected workers**, adding 5,809 notices to the New York/Georgia
candidate. Two isolated replays matched these counts, the notice and version
fingerprints, and the exception ledger hash. Integrity is `ok` with zero
foreign-key errors. The
[full replay report](../data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-v1-report.json)
and [10,558-row exception ledger](../data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-v1.exceptions.jsonl.gz)
preserve source accounting and every held row. The notice-content fingerprint
is `393d70e62a391e62656699e8e9e80a01823571b0b69b9d4408760c339ad1bcab`.

## Maryland and Missouri follow-up

[Maryland's official archive](https://labor.maryland.gov/employment/warn.shtml)
lists 2010–26; the staged database already has 1,398 Maryland notices across
those years through July 7, 2026. The live 2026 table has 20 rows dated after
July 7 (through September 14), so a normal current-page refresh can add them.
The archive does not reveal a comparable historical gap. Its current log mixes
WARN, ESA, and other dislocations, so a refresh must retain source categories.

[Missouri's official annual pages](https://jobs.mo.gov/employer/warn) list
2019–26, while the staged database has only 2025–26 Missouri notices (72).
The 2019–24 agency tables currently show 297 rows (26, 162, 22, 10, 37,
and 40 by year). The agency also links a 1997–2018 workbook, now pinned for
[source review](mo-official-source-review-2026-09-24.md). Its fiscal sheets
extend into June 2019, but include rows that do not establish an employer
WARN filing, so they are not yet admitted. The annual pages render in a
browser, but direct file fetches currently return an Imperva challenge; no
exact older annual HTML page has been pinned. The agency says previous
notices can be requested from its records custodian.

The public database and site have not been replaced.
