# Date eligibility by state: v7 Nevada role checkpoint

The [state table](date-eligibility-state-summary-2026-09-23-v7.csv) summarizes the read-only timing gate on the isolated v7 source-only candidate. Two runs, including one in a fresh hash-locked Python environment, matched byte for byte for the timing CSVs. The same pinned v6 Texas agency bundle and `--observed-at 2026-09-22` were used; v7 changes the Nevada date role and BLN admission guard only.

| Measure | Rows |
| --- | ---: |
| Canonical notices | 86,435 |
| Legal notice date missing | 21,150 |
| Legal notice date present, precision unassessed | 60,448 |
| Effective start missing | 11,572 |
| Effective start present, precision unassessed | 67,814 |
| Provisional formatted notice/start pairs | 53,937 |
| Source-supported notice/start pairs | 4,815 |
| Source-supported notice/end pairs | 1,251 |

Compared with v6, all state notice and worker totals remain unchanged. Nevada's 295 previously nonblank `Received Date` values are now stored as agency receipt facts rather than canonical legal notice dates. This removes 287 provisional timing pairs and three date-dependent links. The prior links are retained in the [Nevada review sheet](nv-legacy-link-review-2026-09-23.csv). Source-supported timing is unchanged because Nevada did not enter the strict cohort. The frozen Nevada raw table has 297 observations under 296 keys; the official 2021 format contains a separately labeled notice date that the current projection does not preserve. See the [source review](next-receipt-date-source-review-2026-09-23.md).

The 4,815 strict starts remain Florida archive HTML **2,552**, South Carolina's 2026 PDF **26**, reviewed New York filings **3**, and pinned Texas annual workbooks **2,234**. Strict ends remain **1,251**. These are reported action dates and intervals, not verified individual employee receipt or separation dates. A blank legal notice date is an explicit unknown, not an assumption that no notice was given.
