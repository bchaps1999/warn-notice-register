# Agency-only rebuild checkpoint

The public v1 candidate now starts from preserved state and agency source
artifacts. Big Local News (BLN) data no longer supplies canonical notices,
worker counts, date repairs, or admission keys. Open-source scraper and parser
software may still be used to collect and interpret agency publications.

## Input and decision boundary

The immutable input is
`data/source_snapshots/2026-09-23-agency-only-il-reviewed-v4.tar.gz`, SHA-256
`270a36e974e1555f02fec93da013f19b17e0a1f9ee83a8a1fc91dc7754cb7071`.
Two independent derivations of the 1,960-file bundle matched byte for byte.
It removes the integrated BLN CSV, the GitHub Flow historical raw CSVs, old
database overlap keys, and New York decisions tied to BLN row hashes. It also
removes BLN-hosted historical rows embedded in the Texas and Ohio raw captures.
Georgia and Tennessee are projected to the 210 and 92 direct agency rows
before their pinned BLN historical append, with exact cache correspondence
checks. The mixed Iowa, Kentucky, and Oregon raw files are held in full until
their direct-source boundary can be proved. Five upstream collectors that append
BLN data are paused before fetching.
Texas retains 2,475 rows from pinned 2020–26 state workbooks; Ohio retains 56
current state list rows. The Texas manifest pins the resulting raw CSV.

The offline rebuild rejects old bundles containing the prohibited data and
checks Texas and Ohio raw rows for those embedded histories. The `backfill`
and `backfill-bln` CLI ingestion commands have been retired. Louisiana's
official table retains an independent manifest and reviewed-row guard.
New York's earlier BLN-specific date and duplicate decisions do not run;
those source documents remain available to support a future agency-native
projection. Kansas worker counts previously repaired using BLN historical raw
are not carried forward.

## Candidate impact

The corrected v4 isolated replay produced **60,324 notices, 68,095 versions,
and 6,235,218 affected workers**. Integrity is `ok`; foreign-key errors are
zero. Its 3,725-entry exception ledger has zero unaccounted current-raw and
agency-cache source rows (SHA-256
`38e7188061f688c4d6eb64d2ea2c43ee111c267e53c24b096deb2e6eb50c0834`).
The separate agency observation table has 974 Iowa/Louisiana rows: 31 admitted
Louisiana notices, 935 Iowa identity holds, six Louisiana event holds, one
annotation, and one rescission. A provenance scan found no BLN source URL, source
identity, or marker in current notice details. The mixed Iowa, Kentucky, and
Oregon raw captures contribute no canonical rows. A second isolated replay
matched the 60,324 notices, 68,095 versions, 6,235,218 workers, source-row
accounting, admitted-coverage rows, and the exact exception checksum. The
stable notice and version fingerprints are
`fe42e2802525b25b20588f1162087870f279d529ef292566946d65d672287633`
and `521771c881fe0b163d64eef04131c1b4a7fa39646b37c958531f34a464a9360a`.
The full Python suite passed **321 tests** after the agency-only guards.
Two independent CSV exports match byte for byte across all 49 files (tree
SHA-256 `08de20ddd757258c5b72ace426f050c6b61cf42e9f5dda97fbc2a00d994076f9`).
The national CSV contains 60,324 records and the observation CSV contains 974.
The [admitted coverage table](agency-only-admitted-coverage-2026-09-23.csv)
breaks notices and workers out by state, source URL, and notice year. The
[exception summary](agency-only-source-exceptions-2026-09-23.csv) gives source
row exclusions by state, source, year where known, and reason; neither table
counts all U.S. WARN filings.

| State | Previous BLN-containing candidate | Agency-only v4 | Change |
| --- | ---: | ---: | ---: |
| TX | 7,319 | 2,244 | -5,075 |
| NY | 5,888 | 1,065 | -4,823 |
| GA | 4,617 | 210 | -4,407 |
| KS | 2,206 | 206 | -2,000 |
| OR | 1,187 | 0 | -1,187 |
| OH | 2,667 | 1,584 | -1,083 |
| TN | 1,086 | 92 | -994 |
| KY | 806 | 0 | -806 |
| MI | 892 | 103 | -789 |
| AZ | 964 | 189 | -775 |
| CT | 617 | 28 | -589 |
| LA | 613 | 31 | -582 |
| ME | 552 | 9 | -543 |
| IA | 375 | 0 | -375 |
| All states | 85,981 | 60,324 | -25,657 |

The previous candidate also included 131 Kansas worker-count fills totaling
25,141 workers from the historical raw snapshot; those decisions are retired.
The nationwide worker total is 2,694,479 lower than in the prior candidate.
These differences are coverage reductions, not estimates of false BLN rows or
missing U.S. WARN filings. The agency-only candidate needs new agency source
acquisition and coverage review before publication.

The checked-in public database, CSVs, and site have **not** been replaced by
this candidate. Cross-artifact site and dump reconciliation, source coverage
decisions, and clean-checkout validation remain release gates.
