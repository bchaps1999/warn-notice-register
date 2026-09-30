# KY and TX raw captures, 2026-09-23

`raw.tar.gz` holds two members copied byte for byte from the superseded
candidate bundle `data/source_snapshots/2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz`
(SHA-256 `4f9ccb698ea9d035514bb2c18d65d3d39ad66a7a9001691544580379ed9dc752`),
which was removed from the working tree after v1.2.0 and remains at
[commit d919111](https://github.com/bchaps1999/warn-notice-register/blob/d919111/data/source_snapshots/2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz).
The v1.2 source bundle has no `raw/ky.csv`, and its agency-only `raw/tx.csv` is
a different projection, so these tests keep the capture they were written against.

| Member | Bytes | SHA-256 | Used by |
| --- | ---: | --- | --- |
| `raw/ky.csv` | 154174 | `b3af80ed97f9aaa599985d7650d60d12702901d1f8182ed04a51943c2d9ed226` | `tests/test_ky_source_roles.py` |
| `raw/tx.csv` | 892732 | `9c3989392d65a241cf8acd66cfacb2a9e1eb8412242c24acdfe1d5c5217a454c` | `tests/test_tx_source.py` (matches `raw_csv_sha256` in `data/source_snapshots/tx/manifest.json`) |

The tarball is deterministic (mtime 0, no owner names). Do not edit it; add a
new dated fixture if a test needs a different capture.
