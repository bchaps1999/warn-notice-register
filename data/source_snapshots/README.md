# Frozen WARN source inputs

`2026-09-22-ca-nj-refresh.tar.gz` is a checksum-verified input bundle for the
offline rebuild. It contains 1,981 files: the locally saved raw state CSVs,
historical backfills, cached agency artifacts, SC PDFs, IL monthly reports, and
a transitional historical-overlap policy. California and New Jersey raw CSVs
were recaptured on September 22, 2026; most other current raw CSVs were last
captured in July or August 2026. It is therefore a mixed-time comparison
snapshot, not a complete as-of-September-22 state-site archive.

SHA-256:
`3410bef41ae2eebc584434b9cf780467597f2bd7a3421ede624b14ace9767741`

Verify before use:

```bash
python -m warnlive.migrate.source_bundle verify \
  data/source_snapshots/2026-09-22-ca-nj-refresh.tar.gz
```

The bundled overlap policy was generated from the existing database's accepted
keys and CA/NY archive URLs. It contains no canonical notice rows, but it
means this is **not** an independent reconstruction of past curation. The
rebuild command creates an isolated candidate and must not replace the
published database without the reconciliation described in
[`docs/warn-remediation-2026-09.md`](../../docs/warn-remediation-2026-09.md).
