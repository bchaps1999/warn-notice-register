# Frozen source inputs

The current v1.2.0 data release replays the [v1.2 source bundle](2026-09-30-v1.2-source-bundle.tar.gz) (SHA-256 `0414c3f55de71bd34993b260360eb5c4d8b8ab4dd626a9f72d623ec7e4f3026b`). The bundle holds 2026-09-30 captures where they keep every released notice, plus new archives for Wisconsin 2016–2019 and Nebraska. The release also relies on:

- its [replay report](2026-09-30-v1.2-candidate-report.json);
- its [exception ledger](2026-09-30-v1.2-candidate.exceptions.jsonl.gz);
- its [release manifest](2026-09-30-v1.2.0-release-manifest.json);
- the [Kansas portal evidence](2026-09-30-ks-portal-evidence.tar.gz);
- the dated [Nebraska report](2026-09-30-ne-ndol-reports/manifest.json) and [Nevada 2021 transcription](2026-09-30-nv-2021-transcription/manifest.json) evidence.

The [v1.2.0 release notes](../../docs/release-v1.2.0-2026-09-30.md) and [candidate record](../../docs/candidate-v1.2-2026-09-30.md) explain the totals and remaining limits.

## Historical v1.1.x inputs

v1.1.0 and v1.1.1 used the [strict Kansas and Kentucky source bundle](https://github.com/bchaps1999/warn-notice-register/releases/download/v1.1.0/2026-09-24-strict-ks-ky-source-bundle.tar.gz). v1.1.0's [replay report](https://github.com/bchaps1999/warn-notice-register/releases/download/v1.1.0/2026-09-24-strict-ks-ky-candidate-report.json), [exception ledger](https://github.com/bchaps1999/warn-notice-register/releases/download/v1.1.0/2026-09-24-strict-ks-ky-candidate.exceptions.jsonl.gz), and [release manifest](https://github.com/bchaps1999/warn-notice-register/releases/download/v1.1.0/2026-09-24-ks-ky-review-release-manifest.json) record that release's accounting. v1.1.1 added the [dated quality evidence](2026-09-24-quality-evidence/manifest.json), recorded in its [release manifest](https://github.com/bchaps1999/warn-notice-register/releases/download/v1.1.1/2026-09-25-quality-release-manifest.json). Reproduce either with its release tag's code.

## Historical v1.0.0 input

The release rebuilds from
[`2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.tar.gz`](https://github.com/bchaps1999/warn-notice-register/releases/download/v1.0.0/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.tar.gz),
SHA-256 `858d1493a86f2a8012f2ce50844b316ab36731ee4482987a93893baedc457de7`.
It contains 2,016 checksum-listed source files and declares
`admission_inputs = agency-only-v1`. It excludes the integrated Big Local News
CSV, old BLN GitHub Flow raw rows, and old-database overlap policies.

The [replay report](https://github.com/bchaps1999/warn-notice-register/releases/download/v1.0.0/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1-report.json)
pins 71,490 notices, 79,261 versions, 7,482,177 reported affected workers,
zero inferred links, and database integrity `ok`. The
[compressed exception ledger](https://github.com/bchaps1999/warn-notice-register/releases/download/v1.0.0/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.exceptions.jsonl.gz)
accounts for 11,888 held source rows. Two isolated replays produced matching
stable notice/version fingerprints, exception bytes, and SQLite bytes.

The agency artifacts under `ga/`, `il/`, `mo/`, `ny/`, `oh/`, `or/`, `tn/`, and
`tx/` preserve original or agency-supplied sources and their custody metadata.
The final bundle, rather than a derived CSV, is the release input. The
[release notes](../../docs/release-v1-2026-09-24.md) describe admitted and
excluded coverage, material changes from the former main-branch product, and
remaining gaps. The [assembly contract](../../docs/rebuild-contract.md)
defines the replay and output checks.

Only the current release's bundle, reports and manifest are kept at the top
of this directory. Superseded candidate bundles, reports and exception ledgers,
including the BLN-containing checkpoints
`2026-09-23-ia-la-ny-first-transit-reviewed.tar.gz` and
`2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz`, were removed from the working
tree after v1.2.0. Release inputs are attached to their
[GitHub releases](https://github.com/bchaps1999/warn-notice-register/releases);
every other file remains at
[commit d919111](https://github.com/bchaps1999/warn-notice-register/tree/d919111/data/source_snapshots).
Current code replays only agency-only bundles; reproduce an earlier release
with its tag's code. The two raw captures that tests still use are copied into
[`tests/fixtures/ky-tx-raw-2026-09-23`](../../tests/fixtures/ky-tx-raw-2026-09-23/README.md).
