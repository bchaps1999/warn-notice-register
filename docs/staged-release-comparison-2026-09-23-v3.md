# Latest staged source-only candidate versus current local database

This is a diagnostic comparison before any promotion. The current local
`data/warn.sqlite` has SHA-256
`6bd2ed4f489b1ba21ba65c5e0993768dee26d8f4ca46fe7808995081f2961f50`.
The staged candidate is the first of two fixed-code isolated replays,
`/private/tmp/warn-nj-role-v3-a-20260923.sqlite`, from the reviewed source
bundle at `--observed-at 2026-09-22 --source-only`. The earlier
[comparison](staged-release-comparison-2026-09-23.md) predates the New Jersey
date-role transition; its totals should not be combined with this one.

| Measure | Current local DB | Staged candidate | Difference |
| --- | ---: | ---: | ---: |
| Notices | 92,271 | 86,436 | -5,835 |
| Reported affected workers | 9,701,472 | 9,006,043 | -695,429 |
| Non-null legal notice dates | 75,391 | 67,363 | -8,028 |
| Non-null effective starts | 77,015 | 74,862 | -2,153 |
| Non-null effective ends | 0 | 2,647 | +2,647 |

The [state-by-state table](staged-candidate-vs-current-db-2026-09-23-v3.csv)
includes these five measures for every state. The largest notice-count
differences are Washington -1,409, Illinois -847, Kansas -604, Iowa -590,
Kentucky -473, New York -471, Michigan -468, and Georgia +452. These are
source coverage and admission diagnostics, **not** error estimates. The
candidate intentionally holds unresolved source observations and no longer
turns New Jersey's yearless posting month into a legal notice date.

The candidate records 2,647 effective ends, including 1,241 supported
Florida archived ranges, two reviewed New York filing ends, and 63 ordered
New Jersey effective ranges. A stored end is not automatically eligible for
notice-to-end research: the strict cohort has only 1,243 rows because the
notice date and end each need source-supported day evidence. All New Jersey
rows remain ineligible while their legal notice date is unknown.

The two fixed-input database replays have identical semantic fingerprints and
exception ledgers; the generated national/state CSVs and site files also
match byte for byte. The [progress log](rebuild-progress.md) records those
hashes and remaining gates. In particular, 41 old New Jersey keys nominate
multiple new raw observations, and source documents must establish whether
they are separate filings, phases, or revisions. Iowa, New York, Louisiana,
and broader unmatched source queues need similar dispositions. Dependency
pinning and a fresh-environment replay remain outstanding. The published
database, committed exports, and site have not been replaced by this staged
candidate.
