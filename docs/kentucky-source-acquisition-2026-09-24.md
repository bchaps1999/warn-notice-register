# Kentucky agency source acquisition

**Status:** Current agency CSV captured and projected into an isolated source-only candidate; scheduled collector remains disabled and the published release is unchanged.

The [Kentucky Career Center WARN page](https://kcc.ky.gov/Pages/News.aspx) currently links an agency-hosted [2026 CSV report](https://kcc.ky.gov/WARN%20notices/WARN%20Notices%202026/WARN%20Report-2026-09-16-09-00-01.csv), a [1998–2016 archive workbook](https://kcc.ky.gov/WARN%20notices/2017%20WARN%20docs/Kentucky%20%20WARN%20Report%20-Tracking%20Form%20%281998-2016%29.xlsx), and a link to a [2023 workbook](https://kcc.ky.gov/WARN%20notices/WARN%20Notices%202023/WARN%20Report%2007202023.xlsx) whose visible label on the page is stale. These are original agency paths, but the current report link may be replaced as the year progresses. The [pinned 2026 CSV and manifest](../data/source_snapshots/ky/official-2026/manifest.json) preserve its exact URL, capture date, bytes, and SHA-256 `a40a050d1b10a2ea4d1ec54302d548c4499534d1e16411840b9c383d5d2b2da5`.

The CSV has 35 physical rows and 35 distinct agency notice numbers. It uses 13 columns, including `Date Received`, `Projected Date`, `Number of Employees Affected`, and `Notice URL`. Thirty-three rows name a Kentucky county; two explicitly say `Out of the State County` and are held from Kentucky notice totals. Thirty-one rows were received in 2026 and four in December 2025. This is a current linked report snapshot, not proof of complete annual coverage. `Date Received` is agency receipt, not a verified legal employer notice date; `Projected Date` is a projected action date. The county is a county-level location, not a verified worksite address.

The [strict bundle overlay](../data/source_snapshots/2026-09-24-strict-ks-ky-source-bundle.tar.gz) admits the 33 in-state rows as distinct agency notice numbers and leaves both out-of-state rows in a source observation and [exception ledger](../data/source_snapshots/2026-09-24-strict-ks-ky-candidate.exceptions.jsonl.gz). The [isolated replay report](../data/source_snapshots/2026-09-24-strict-ks-ky-candidate-report.json) adds exactly 33 notices and 4,316 reported workers over the Kansas candidate, with all 35 source rows accounted for and no changes to existing records. All 33 legal notice dates are null. This is a source-backed snapshot projection, not an assertion that distinct agency numbers prove unrelated layoff events or unique affected people.

The [Kentucky PY 2024 WIOA report](https://kwib.ky.gov/Documents/PY24%20WIOA%20Annual%20Narrative_Kentucky.pdf#page=11) reports 35 WARN notices affecting 2,363 workers. It is a program-year aggregate and cannot establish which calendar-year rows belong in the register.

The current upstream fetch is deliberately blocked in `warnlive/fetch/__init__.py` because its history mixes agency rows with an unsupported third-party mirror. The frozen raw projection has 828 rows, but it is not an agency-owned source inventory; source-only bundle validation rejects it. The new source-only projector carries each agency file and row pointer into the candidate independently of that old path. The fetch block should remain until a complete agency-only collector has row-level provenance and partial-capture safeguards.

## Next source checks

1. Acquire and hash the archive workbooks. Request or locate agency-owned annual reports for 2017–2025 and a complete 2026 inventory at a fixed capture time.
2. Inspect headers, notice IDs, receipt and action date fields, amendment markers, source document URLs, and multi-site rows. Reconcile physical rows and annual counts; keep different counting units separate.
3. Reconcile later agency captures against the 35 pinned notice numbers, keeping revisions and multi-site rows separate from new filings. Continue holding explicit out-of-state rows.
4. Implement a `fetch/custom/ky.py` adapter that enumerates the pinned agency files and projects to the existing 20-column normalization schema, with explicit row-to-file provenance. Extend source-bundle validation to verify that provenance rather than accepting a raw CSV on its own.
5. Replace the paused upstream collector only after the agency-only adapter passes file-schema, count, freshness, identity, and partial-capture checks. Keep the old mixed-source collector disabled.

No request has been sent. An agency records request should ask for public annual WARN inventories and document-to-notice mapping, while excluding employee lists and private worker details.
