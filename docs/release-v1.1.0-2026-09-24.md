# WARN Notice Register v1.1.0 — strict source replay

This release promotes the frozen Kansas and Kentucky agency-source replay observed on 2026-09-24. The database contains **74,828 admitted notices, 82,241 versions, 7,815,994 reported affected workers, 1,009 source observations, and zero notice links**. Its 47 represented states are states with admitted records, not a count of jurisdictions with complete historical coverage.

The [source bundle](../data/source_snapshots/2026-09-24-strict-ks-ky-source-bundle.tar.gz), [replay report](../data/source_snapshots/2026-09-24-strict-ks-ky-candidate-report.json), [exception ledger](../data/source_snapshots/2026-09-24-strict-ks-ky-candidate.exceptions.jsonl.gz), and [release manifest](../data/source_snapshots/2026-09-24-ks-ky-review-release-manifest.json) preserve the inputs, dispositions, and output hashes. The [system review](system-review-2026-09-24.md) records the policy decisions and unresolved source groups.

| Measure | v1.0.1 | v1.1.0 | Change |
| --- | ---: | ---: | ---: |
| Admitted notices | 71,490 | 74,828 | +3,338 |
| Versions | 79,261 | 82,241 | +2,980 |
| Reported workers | 7,482,177 | 7,815,994 | +333,817 |
| Source observations | 974 | 1,009 | +35 |

The strict replay holds 97 previously published notice keys whose source rows have conflicting action dates under one key. Kansas's complete official portal capture has 910 IDs: 834 are admitted and 76 are held for ambiguous event identity or a missing notice date. Twenty-seven of those held IDs were in v1.0.1. Kentucky's pinned official CSV has 35 agency-numbered rows: 33 rows naming Kentucky counties are admitted, with 4,316 reported workers, and two explicitly out-of-state rows are held. Compared with the Kansas-only strict candidate, Kentucky adds exactly 33 keys and changes no preexisting record.

The 124 published-only keys have documented hold dispositions. This is a conservative source-identity change; a held row is not evidence that no WARN filing occurred. Kansas workforce-area labels do not establish individual layoff sites. Kentucky's one pinned CSV is not a complete historical inventory. Ohio's 2023–25 agency row inventory and other unresolved source groups remain coverage gaps.

## Verification and operations

The staged SQL dump restored with matching schema and ordered contents across all seven tables. SQLite integrity was `ok` and the foreign-key check found zero errors. A second build reproduced all 50 CSV and 568 site JSON files byte for byte. The standard release verifier reconciled the database, exports, and site data. An independent review checked Kentucky's 35 source rows, all 947 captured Kansas HTML hashes, the 834/76 portal-ID partition, and a Kansas notice directly against its source page. Python tests, frontend tests, and the production frontend build passed.

The prior regression snapshot flags three intentional policy changes: Michigan's admitted count falls from 103 to 100 when conflicting key groups are held; Kansas workers rise from 12,851 to 120,009 after full portal capture; and Kansas missing-worker counts fall as detail-page headcounts enter the register. The reviewed candidate snapshot replaces the old baseline with this data release. Routine regression thresholds remain unchanged.

This offline replay has no live collector-run history. A missing or overdue success time in the health report must not be read as a failed filing search or as proof of no notices. Site data in the release staging area is pinned to 2026-09-24; deployment regenerates it for the deployment day and versions requests from the generated JSON contents, so a later-day build refreshes browser caches even when the database dump is unchanged. The v1.0.1 release commit and SQL dump remain the rollback point.
