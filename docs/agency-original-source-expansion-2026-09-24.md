# Original-source coverage expansion, 2026-09-24

This staged, unpublished candidate extends the [agency-only OR/TN/NY candidate](agency-coverage-expansion-2026-09-23.md). Its immutable input bundle is
[`2026-09-24-agency-only-ny-ga-v1.tar.gz`](../data/source_snapshots/2026-09-24-agency-only-ny-ga-v1.tar.gz), SHA-256
`d57cecd49ee6fa3a0ea3cb16fbaf5768faee0982b186d078df8093fd660cd252`.
It contains 1,994 files, preserving the earlier source files and adding direct
annual [New York DOL dashboard exports](https://dol.ny.gov/warn-dashboard) and
five [Georgia TCSG-linked archive PDFs](https://www.tcsg.edu/worksource/rapid-response/).
No removed BLN row is an admission input.

## Source accounting and admission

| Source | Verified source rows | Admitted rows | Held rows | Rule |
| --- | ---: | ---: | ---: | --- |
| New York, 2006–26 annual CSVs | 9,044 | 3,175 | 5,869 | Complete single-employer observations with a unique site/action/posting combination; screen current canonical employers and site/action/worker overlaps after other ingestion. Dashboard index and observation hash are not legal filing IDs. |
| Georgia, 2018–22 PDFs | 789 published IDs | 724 | 65 | Complete parsed PDF rows with a published GA ID; 32 lack a reliable table row and 33 have unresolved table text. Screen already represented IDs and current IDless events. |

Every source row has a stable pointer in the input or exception ledger. New
York's CSVs retain both repeated worker columns and require equal values.
Georgia dates are reported separations; the PDFs do not establish a notice
date. The excluded historical CSV was used only as a diagnostic for parsing,
never as a source of admitted values.

## Coverage and limits

The preceding candidate had 60,945 notices and 6,302,280 workers. New York's
all-year projector supersedes the 2022–24-only projector, so its 3,175
admitted rows produce a **net 2,846-notice** gain. Georgia adds **724** rows.
The combined gain is **3,570 notices**, yielding **64,515 notices**, **72,286
versions**, and **6,681,274 workers**. The first corrected replay passed
`PRAGMA integrity_check` and had zero foreign-key errors. Its canonical
notice and version fingerprints were
`f055bdcc71cc01344391d9a5a5753c1283349476594884f1e60002b6c59ec1f6`
and `34201faccf4cedcde5ca2acab480da9e0c62b0f667036b052cc82d9cebe740fc`.
Two isolated replays with the same bundle and observation day produced these
same counts, source accounting, and notice/version fingerprints. The
[replay report](../data/source_snapshots/2026-09-24-agency-only-ny-ga-v1-report.json)
records state/year coverage; its 10,188-row [compressed exception ledger](../data/source_snapshots/2026-09-24-agency-only-ny-ga-v1.exceptions.jsonl.gz)
decompresses to the SHA-256 stated in the report. The report's exception path
is the original temporary replay location; the linked copy is preserved here.

The earlier BLN-containing candidate had 85,981 notices. The remaining
comparison gap is therefore about 21,466 notices, not a verified count of
missing legal filings. Texas before 2020, Georgia before 2018, and Ohio
2015–25 still need original files or row-level reconciliation. The live
[Kansas WARN search](https://www.kansasworks.com/search/warn_lookups?commit=Search&q%5Bnotice_eq%5D=true)
shows 910 WARN rows, while 1,251 of 2,126 rows in the excluded old capture
are labeled Non-WARN. A capture attempt hit HTTP 429, so no new Kansas rows
were admitted. The portal should be recaptured slowly with listing and detail
pages once it permits access.

The public database and site were not replaced.
