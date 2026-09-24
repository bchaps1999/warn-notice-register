# Staged source-only candidate versus current local database

This is a diagnostic comparison, not a promotion decision. The current local
`data/warn.sqlite` (SHA-256
`6bd2ed4f489b1ba21ba65c5e0993768dee26d8f4ca46fe7808995081f2961f50`)
is compared with the isolated source-only candidate
`/private/tmp/warn-ft-v7-fl-a-20260923.sqlite` (SHA-256
`06e0a9dc9cd67a440014e20f0209e1777d02d82944f82fcb51f090a95270b3b9`).
The candidate comes from the pinned First Transit bundle at fixed
`--observed-at 2026-09-22`, plus the v7 date-evidence and Florida archive
source-field rules. The [state-level table](staged-candidate-vs-current-db-2026-09-23.csv)
records notices, workers, notice-date coverage, and effective-start coverage
for every state.

| Measure | Current local DB | Staged candidate | Difference |
| --- | ---: | ---: | ---: |
| Notices | 92,271 | 86,388 | -5,883 |
| Reported affected workers | 9,701,472 | 8,998,760 | -702,712 |
| Non-null notice dates | 75,391 | 69,630 | -5,761 |
| Non-null effective starts | 77,015 | 74,814 | -2,201 |
| Non-null effective ends | 0 | 2,639 | +2,639 |

The largest notice-count differences are Washington -1,409, Illinois -847,
Kansas -604, Iowa -590, Kentucky -473, New York -471, Michigan -468, and
Georgia +452. These are **not** error counts or claims that one database is
truer. The candidate deliberately changes admission rules and uses a frozen
source bundle; the current database reflects prior live and historical paths.
The row-level exception ledger and source correspondence work must explain
held, duplicated, superseded, and missing observations before release. Exact
old-count agreement is not the target.

This comparison also highlights the date contract: the candidate now retains
2,639 effective ends, including 1,241 Florida archived `thru` ranges and two
reviewed New York filing ends. A non-null end alone does not establish that a
notice is eligible for exact-day timing; both endpoint roles and precision
must pass the independent date-evidence gate. The current DB has no effective
end field with non-null values.

Next: reconcile the large state deltas to source dispositions, correct the
New Jersey posting-month role before promotion, review held Iowa/NY/LA
identity cases, and compare two fixed-input CSV/site builds. The published
database, committed exports, and site have not been replaced by this staged
candidate.
