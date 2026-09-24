# New Jersey frozen-source row correspondence

This is a source-row audit before any change to New Jersey notice-date semantics.
It compares the complete prepared rows in `raw/nj.csv` and
`backfill/raw/nj.csv` from the frozen bundle. The report is generated without
writing to the candidate database or source bundle.

## Inputs and reproduction

- Bundle: `data/source_snapshots/2026-09-23-ia-la-ny-first-transit-reviewed.tar.gz`
- Bundle SHA-256: `95d242fcb2cb6485afb6f9cb0da2625fffc7607565c3a372e8e5059642cdeba5`
- `raw/nj.csv` SHA-256: `07cb172031ab83e57a5b9756653ebd1a1a0c87823ac5c005902c293165ad18a6`
- `backfill/raw/nj.csv` SHA-256: `97d4154967601cf3650f586f18aee2edb400a070ed598deab69cfd068dd625d8`
- Read-only candidate DB: `/private/tmp/warn-ft-v7-fl-a-20260923.sqlite`; the
  consistent SQLite backup used for nominations has SHA-256
  `bf5c1f5b12538fcb5fda35cbb3974f7683c0dc658705682f1c2a539860cdb61b`.

Run from the repository root:

```sh
.venv/bin/python -m warnlive.migrate.nj_reconcile \
  --bundle data/source_snapshots/2026-09-23-ia-la-ny-first-transit-reviewed.tar.gz \
  --candidate-db /private/tmp/warn-ft-v7-fl-a-20260923.sqlite \
  --out-dir /private/tmp/nj-source-correspondence-20260923-v2
```

This writes `nj_source_correspondence.csv` and
`nj_source_correspondence.json` to the output directory. Every prepared row
has its artifact and artifact hash, prepared-row ordinal, SHA-256 of the full
prepared-row JSON, occurrence counts, disposition, raw JSON, normalization
result or parse error, and existing candidate source ID, dedupe key, and
version nominations. JSON uses sorted keys and compact UTF-8 encoding; the
row SHA is a hash of that representation, not a physical CSV line hash.
The ordinal is the transformer's prepared-row position after empty rows are
removed, not a CSV line number.
The CSV SHA-256 is `41dcfd94444cb5ea0b9290a7a35f2220eb70e62beafeb01ad0bd201ef48d75ac`;
a second read-only run against the same frozen inputs yielded the same CSV and
SQLite snapshot hashes.

| Artifact | Prepared rows | Exact | Ambiguous | Unmatched | Parse failures |
| --- | ---: | ---: | ---: | ---: | ---: |
| `raw/nj.csv` | 2,378 | 2,304 | 50 | 24 | 13 |
| `backfill/raw/nj.csv` | 2,355 | 2,304 | 50 | 1 | 2 |
| Total row occurrences | 4,733 | 4,608 | 100 | 25 | 15 |

`exact` means a complete row occurs once in each artifact. `ambiguous` means
the row occurs in both artifacts but at least one artifact contains duplicate
copies, so a unique occurrence pair cannot be assigned. `unmatched` means no
complete-row counterpart exists in the other artifact. These dispositions do
not say whether rows describe the same real-world WARN event. Parse failures
remain in the CSV with their raw content and error; no normalization result is
invented for them.

Candidate source IDs and dedupe keys come from the existing NJ notices in the
candidate database. Their version nominations include the notice ID, version,
and existing raw-record hash. A key-only nomination can depend on the current
inferred notice date. Neither a shared employer/date nor a matching dedupe key
is used to infer source-row or event identity. Re-running against a different
candidate database may change nomination columns while source correspondence
and its artifact hashes remain tied to the frozen bundle.
