# Kansas review release candidate, 2026-09-24

**Status:** Assembled and verified locally; no published data, site, tag, or remote branch was changed.

The assembled candidate is at `/private/tmp/ks-review-release`. Its [manifest](../data/source_snapshots/2026-09-24-ks-review-release-manifest.json) records the SHA-256 of the staged SQL dump, 46 state CSVs, national and source-observation CSVs, site JSON, health files, source bundle, portal evidence, code and reference inputs. The manifest SHA-256 is `a56f06d96f6e0c21517d8eb325f55129563b89d641ccbd55a5e0845d7e86c679`. The path is a local review staging area; the manifest and source evidence are preserved in the repository.

| Measure | Published | Candidate | Change |
| --- | ---: | ---: | ---: |
| Canonical notices | 71,490 | 74,795 | +3,305 |
| Versions | 79,261 | 82,208 | +2,947 |
| Reported workers | 7,482,177 | 7,811,678 | +329,501 |
| Kansas notices | 206 | 834 | +628 |

The [published-key reconciliation](../data/source_snapshots/2026-09-24-ks-published-key-reconciliation.json) compares every canonical dedupe key: 71,366 shared, 124 published-only, and 3,429 candidate-only. All 124 published-only keys have a documented hold disposition. Twenty-seven are Kansas portal IDs held in the companion source evidence; the other 97 are same-key date conflicts in the strict exception ledger. The net change is 3,305 notices. Key counts are identity-accounting checks, not proof that every new key is a distinct legal filing.

## Verification completed

- The [frozen Kansas source bundle](../data/source_snapshots/2026-09-24-strict-ks-portal-source-bundle.tar.gz) passed source-bundle verification: 2,016 files.
- The Kansas database hold check passed on the staged candidate. It fails on the current public database, as intended for the 27 held Kansas IDs.
- Full release reconciliation regenerated and byte-compared every staged CSV and site JSON file against the database. It returned 74,795 notices, 7,811,678 reported workers, 46 represented states, and 974 source observations.
- The compressed SQL dump was restored to a separate database. All seven tables, including versions, observations, and run metadata, have matching ordered-row hashes and schema; integrity checks passed with zero foreign-key errors.
- The candidate regression snapshot and pinned-date health files reproduce from the restored database. A production frontend build with the staged site JSON passed.

The [baseline review](../data/source_snapshots/2026-09-24-ks-regression-baseline-review.json) preserves the comparison with the currently published health snapshot. Three automatic checks fail because this is a deliberate source-policy release: Michigan falls from 103 to 100 canonical notices when three conflicting key groups are held; Kansas workers rise from 12,851 to 120,009 with the broader official portal capture; and Kansas's missing-worker share falls sharply because the detail pages report counts. The distinct-employer check warns for Kansas and New York. These changes need an explicit reviewed baseline replacement when the candidate is promoted; weakening the routine thresholds would hide future parser failures.

## Release decision and remaining limits

Promotion would replace the checked-in SQL dump, exports, health snapshot, and release notes together with the collector and admission code. The shared deploy action will then rebuild site JSON for the deployment day; the staged site JSON is pinned to 2026-09-24 for reproducible review. The candidate has no live `state_runs` history, so its health output honestly reports collection success as unknown or overdue until future runs establish it.

The 76 held Kansas portal rows, strict same-key groups, and other source exceptions remain held. Ohio's 2023–25 inventory and the other coverage gaps in the [system review](system-review-2026-09-24.md) are separate follow-up work. No source evidence currently supports treating the candidate totals as complete national WARN coverage.
