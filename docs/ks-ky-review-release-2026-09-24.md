# Kansas and Kentucky review release candidate, 2026-09-24

**Status:** Assembled and verified in `/private/tmp/ks-ky-review-release`, then promoted into the local v1.1.0 repository artifacts. This staging note records the checks before promotion; remote deployment and tagging are separate.

The [manifest](../data/source_snapshots/2026-09-24-ks-ky-review-release-manifest.json) (SHA-256 `86bd462a0c41f419fc95493a74c37eb8715833b7810048ca42239d939862ec10`) records the staged SQL dump, 50 CSVs, 568 site JSON files, health files, source bundle, official source captures, reference files, and code inputs. Site output is pinned to 2026-09-24. The staging directory is temporary; the preserved source bundle and manifest support rebuilding it.

| Measure | Published | Candidate | Change |
| --- | ---: | ---: | ---: |
| Canonical notices | 71,490 | 74,828 | +3,338 |
| Versions | 79,261 | 82,241 | +2,980 |
| Reported workers | 7,482,177 | 7,815,994 | +333,817 |
| Kansas notices | 206 | 834 | +628 |
| Kentucky notices | 0 | 33 | +33 |

## Checks

- The staged SQL dump restored through `warnlive unpack-db`. All seven tables and the schema matched the candidate logically, SQLite integrity was `ok`, and the foreign-key check found zero errors.
- A second build reproduced every staged CSV and site JSON file byte for byte: 50 CSVs and 568 JSON files. The standard release verifier then regenerated and compared the finished trees against the database: 74,828 notices, 7,815,994 workers, 47 represented states, and 1,009 source observations.
- An independent source review matched the 33 admitted Kentucky records against all 35 rows of the official CSV: 33 in-state rows report 4,316 workers; two out-of-state rows remain unlinked and held. It verified all 947 captured Kansas HTML hashes and the exact 834 staged plus 76 held partition of 910 portal IDs. A source-page spot check of `KS:1066` matched Cessna, January 29, 2009, and 2,800 workers in the database.
- The Python suite passed earlier in this review (414 tests). `npm test --prefix site` passed (3 tests), and `npm run build --prefix site` passed. The build reported its existing large-chunk advisory.
- The [regression comparison](../data/source_snapshots/2026-09-24-ks-ky-regression-baseline-review.json) against the current published snapshot has the same three intentional failures as the Kansas-only stage: Michigan 103 to 100 notices from strict held key groups, Kansas workers 12,851 to 120,009, and a large decrease in Kansas missing worker counts. It warns on distinct-employer growth in Kansas and New York. The routine thresholds were not weakened; a candidate-specific baseline must be reviewed and published with the data.

## Promotion boundary

The v1.0.1 database contained 27 Kansas portal IDs held under the new policy. Deploying the new checks against that old database would stop publication. The local v1.1.0 artifacts now pair the SQL dump, exports, health snapshot, release notes, and site data with the collector and admission code. The deployment-day site build should use that day as its `as_of` date. The replayed database has no live `state_runs` history, so its health report cannot claim a recent successful collection.

The held Kansas rows, same-key groups, Kentucky historical workbooks, Ohio's 2023–25 row inventory, and other source gaps remain as described in the [system review](system-review-2026-09-24.md). These totals count admitted source-backed notices, not complete national WARN coverage.
