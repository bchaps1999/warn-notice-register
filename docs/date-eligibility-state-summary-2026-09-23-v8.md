# Date eligibility by state: v8 Massachusetts role checkpoint

The [state table](date-eligibility-state-summary-2026-09-23-v8.csv) summarizes the read-only timing gate on an isolated v8 source-only candidate built from the pinned Texas annual-workbook bundle with `--observed-at 2026-09-22`. The Massachusetts change applies to the current tracker and its separately captured FY2020 workbook. Two database replays, including one in a fresh hash-locked Python environment, match on counts, fingerprints, state summaries, the exception ledger, timing CSVs, exports, and site files. Exact hashes are in the [progress log](rebuild-progress.md).

| Measure | Rows |
| --- | ---: |
| Canonical notices | 86,435 |
| Legal notice date missing | 21,696 |
| Legal notice date present, precision unassessed | 59,902 |
| Effective start missing | 11,572 |
| Effective start present, precision unassessed | 67,814 |
| Provisional formatted notice/start pairs | 53,413 |
| Source-supported notice/start pairs | 4,815 |
| Source-supported notice/end pairs | 1,251 |

Compared with v7, Massachusetts retains 550 notices, 58,197 workers, 528 reported effective starts, and 111 effective ends. All 550 prior keys and source notice IDs are retained. The 546 previously nonblank `RECEIVED`/`Date Received` values leave canonical legal `notice_date`; 524 Massachusetts rows leave the provisional timing cohort. The one current-table received cell with two dates is preserved as ambiguous source text. The [source review](next-receipt-date-source-review-2026-09-23.md) includes a concrete GSK row where the received date is one day after the dated filing.

The parallel deterministic-cleanup change retired heuristic `notice_links` nationwide in this same code checkout. V8's zero-link count reflects that separate change; it is not a Massachusetts date effect. Prior WA/MN/NV link sheets remain historical audit evidence, not active model or matching queues. The source-row exception ledger remains deterministic accounting for held and unresolved observations.

The strict cohort remains 4,815 starts and 1,251 ends because Massachusetts never had source-supported legal notice dates from its tracker receipt field. These dates still represent reported action timing rather than verified individual employee notice receipt or separation.
