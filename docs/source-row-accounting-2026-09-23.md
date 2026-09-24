# Source-row accounting checkpoint (September 23, 2026)

> Historical BLN-containing checkpoint. The current admission policy and
> candidate are documented in the [agency-only rebuild](agency-only-rebuild-2026-09-23.md).

This is an isolated **candidate** checkpoint from the pinned source bundle, not a promoted public database. The source-only rebuild now emits a row-level exception for lower-priority raw rows skipped because their canonical key already exists, and for source rows coalesced within an ingest batch. A skipped same-key row is labeled `matched_existing_key_not_admitted` with `match_basis=canonical_key_only`: this is a conservative exclusion, **not** proof that two filings are the same event. Coalesced rows carry a source-row or prepared-row pointer to the surviving observation.

The build stops on a state normalization, verification, or ingest exception that would otherwise leave a misleading partial row ledger. Ordinary per-row parse failures and explicit verification exclusions remain in the manifest. Before writing the manifest, an automatic gate compares each primary source layer's excluded-row count with its ledger entries and rejects mismatches. It accounts for South Carolina's cached-PDF rows and selected Big Local News (BLN) rows coalesced during ingestion; the frozen input has zero such BLN coalescences. These failure and edge cases have regression tests.

## Frozen replay result

Two isolated source-only rebuilds used `data/source_snapshots/2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz` (SHA-256 `4f9ccb698ea9d035514bb2c18d65d3d39ad66a7a9001691544580379ed9dc752`) and `--observed-at 2026-09-22`. Both have **85,954 notices, 94,369 versions, zero inferred links, and 8,923,252 reported workers**. SQLite integrity is `ok`; foreign-key errors are zero. The notice and version content fingerprints remain identical to the Kansas v11 candidate (`8a738fd42fab7c4c05f9d434e6911fb19ed5dd5c3c9badbd4b7623a282bbe8b7` and `aeb95337825b1020faaeb30d561cd4972aabd928a5790883c22dab7d00358d41`). Both exception manifests have **135,036 entries** and SHA-256 `e131970c38761cf290239a290360f98ff6b9c26cf48a9e22005ffb69be67b861`. A third replay with the automatic gate passed and matched the same fingerprints and exception checksum.

| Source layer | Source rows | Admitted/represented | Row-level exclusions | Accounting check |
| --- | ---: | ---: | ---: | --- |
| Current state raw CSVs | 60,121 | 58,998 new/update/unchanged observations | 1,123 parse, quarantine, or coalesced rows | 0 unaccounted |
| Prior state raw CSVs | 54,974 | 3,602 new observations | 51,372, including 51,146 same-key skips | 0 unaccounted |
| BLN integrated CSV | 88,626 | 8,810 selected source rows | 79,816, including 42,731 same-key skips | 0 unaccounted |
| Cached agency history, six states | 20,194 parsed | 18,419 new observations | 1,775 overlap, ambiguity, or coalesced rows | 0 unaccounted |

The four exclusion counts sum to 134,086. The manifest has 950 further entries: 935 Iowa agency identity holds, eight Louisiana agency annotations/holds/rescissions, five Texas annual-evidence exceptions, one Texas raw-year anomaly, and one missing Louisiana raw-file marker. The Texas raw anomaly and missing-file marker are not counted as excluded current-raw rows. The BLN row equation is `88,626 = 8,810 selected + 79,816 excluded`; its 8,810 selected rows include 8,518 new and 292 updated observations. These are **source-observation counts**, not distinct WARN filings or layoffs. A canonical notice can have multiple versions and can be represented in multiple source layers.

The full Python suite passed **292 tests** after these changes. The checkpoint does not resolve the material state coverage deltas, source date and event identity questions, collector failures, or clean release-commit replay. The checked-in database, exports, and site remain the older artifacts and must not be described as this candidate.
