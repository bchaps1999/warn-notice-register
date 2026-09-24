# Frozen source inputs

The current v1.1.0 data release uses the [strict Kansas and Kentucky source bundle](2026-09-24-strict-ks-ky-source-bundle.tar.gz). Its [replay report](2026-09-24-strict-ks-ky-candidate-report.json), [exception ledger](2026-09-24-strict-ks-ky-candidate.exceptions.jsonl.gz), and [release manifest](2026-09-24-ks-ky-review-release-manifest.json) preserve the candidate's source and output accounting. The [v1.1.0 release notes](../../docs/release-v1.1.0-2026-09-24.md) explain the admitted totals and remaining limits.

## Historical v1.0.0 input

The release rebuilds from
[`2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.tar.gz`](2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.tar.gz),
SHA-256 `858d1493a86f2a8012f2ce50844b316ab36731ee4482987a93893baedc457de7`.
It contains 2,016 checksum-listed source files and declares
`admission_inputs = agency-only-v1`. It excludes the integrated Big Local News
CSV, old BLN GitHub Flow raw rows, and old-database overlap policies.

The [replay report](2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1-report.json)
pins 71,490 notices, 79,261 versions, 7,482,177 reported affected workers,
zero inferred links, and database integrity `ok`. The
[compressed exception ledger](2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.exceptions.jsonl.gz)
accounts for 11,888 held source rows. Two isolated replays produced matching
stable notice/version fingerprints, exception bytes, and SQLite bytes.

The agency artifacts under `ga/`, `il/`, `mo/`, `ny/`, `oh/`, `or/`, `tn/`, and
`tx/` preserve original or agency-supplied sources and their custody metadata.
The final bundle, rather than a derived CSV, is the release input. The
[release notes](../../docs/release-v1-2026-09-24.md) describe admitted and
excluded coverage, material changes from the former main-branch product, and
remaining gaps. The [assembly contract](../../docs/rebuild-contract.md)
defines the replay and output checks.

Two earlier BLN-containing frozen bundles remain as regression fixtures:
`2026-09-23-ia-la-ny-first-transit-reviewed.tar.gz` and
`2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz`. They are **not** v1.0.0
admission inputs. Historical research reports elsewhere in the repository
refer to additional local checkpoints; only the final Ohio-inclusive bundle
is required to rebuild the release.
