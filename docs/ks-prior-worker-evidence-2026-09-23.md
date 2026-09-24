# Kansas prior agency worker counts: v11 candidate

> Historical decision, retired for the [agency-only rebuild](agency-only-rebuild-2026-09-23.md).
> The older `backfill/raw/ks.csv` capture came through BLN GitHub Flow and no
> longer supplies notices or worker-count repairs.

The frozen source bundle contains two Kansas agency table captures under `raw/ks.csv` (206 rows) and `backfill/raw/ks.csv` (2,126 rows). Both carry a numeric `record_number` and a detail URL ending in that number. All 206 current record numbers occur exactly once in the older table; the older table has 2,126 distinct record numbers. The source-only rebuild uses the record number as a **source-row identity**, without asserting that every row is a separate legal WARN filing.

The current capture leaves the worker-count cell blank on 135 of its 206 rows. For **131** of those rows, the older agency capture reports an integer headcount under the same record number, employer, and notice day. The archived counts total **25,141 workers**. The other four current blanks also lack a count in the older capture. The two captures have no conflicting nonblank worker counts in their shared records. For example, current Boeing records 30, 31, 37, and 45 leave counts blank; the older agency rows report 98, 404, 236, and 249 respectively.

The guarded Kansas reconstruction fills only those 131 current blanks. It verifies unique agency record numbers, exact employer and notice-day correspondence, a blank current count, and a nonnegative older count. A changed employer/day or two conflicting reported counts abort the operation. It retains the current source's other fields, adds a `worker_count_evidence` object to `source_details` with the prior row hash and reported count, and appends an interpretation version. It preserves the prior `is_amended` flag because this is not an employer-filed amendment. The preserved source bundle and exception ledger remain the raw evidence; the selected canonical count is a deterministic interpretation of two agency captures.

## Isolated v11 replay

Two source-only replays from the pinned `2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz` bundle at `--observed-at 2026-09-22` matched on:

| Measure | V10 | V11 |
| --- | ---: | ---: |
| Canonical notices | 85,954 | 85,954 |
| Notice versions | 94,238 | 94,369 |
| Reported affected workers | 8,898,111 | 8,923,252 |
| Kansas workers | 153,092 | 178,233 |
| Kansas rows without headcount | 212 | 81 |
| Links | 0 | 0 |

Every canonical key is unchanged. A full-column comparison of `notices` found changes only in `employees_affected`, `source_details`, and `current_version`, each on exactly 131 Kansas rows. The exception ledger remains 40,069 rows with SHA-256 `bfc7fe59cdff3f5958f1e82dd21761ac76afa769b66a56436b490651b7dc71ab`. Both replays had SQLite integrity `ok`, zero foreign-key errors, notice fingerprint `8a738fd42fab7c4c05f9d434e6911fb19ed5dd5c3c9badbd4b7623a282bbe8b7`, and version fingerprint `aeb95337825b1020faaeb30d561cd4972aabd928a5790883c22dab7d00358d41`.

Both isolated exports contained 49 CSV files and 974 scoped Iowa/Louisiana source observations. Both fixed-date site builds contained 568 files. Identical path/content tree hashes were CSV `c0c5a7a13618b4fde9ee56cfc89f651a6180e4fd38b5b283ee255fe6317629c9`, site `fdc916d3e7da74b54782c3a5b347b92d0fd27f745e17e3c6cfc7fbfd16cb994e`, and five timing files `4a504b77f35aeeaf7c198069e2648f6d9dea03d26e86ecd73308bb3ab675a7f1`. Each tree hash is SHA-256 over sorted relative paths, a NUL separator, each file's hex SHA-256, and a newline. The two compressed SQL dumps had identical SHA-256 `368cc78ca5b1ef1c0503972bd2b1eb1f0eecac1c77a29532ff7f9f640a890918`; one restored to 85,954 notices, 94,369 versions, 8,923,252 workers, integrity `ok`, and zero foreign-key errors. CSV and site metadata agreed with database totals. The strict timing cohort remains 4,815 starts and 1,251 ends, with 52,404 provisional formatted start pairs.

The full Python suite passed **282 tests** after this change. These are two replays in the current environment and shared working tree, not a clean release-commit or fresh Ubuntu validation. The candidate, exports, site, and SQL dump remain under `/private/tmp`; no checked-in or deployed artifact was replaced. The [material coverage review](material-coverage-delta-review-2026-09-23.md) still has unresolved Kansas event-identity and coverage questions, including old/new key correspondence and old-only source IDs.
