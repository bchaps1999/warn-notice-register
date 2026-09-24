# Date eligibility by state: v6 source-only checkpoint

The [state table](date-eligibility-state-summary-2026-09-23-v6.csv) summarizes the read-only timing gate on `/private/tmp/warn-role-tx-v6-final-a-20260923.sqlite`, built from the pinned TX annual-workbook bundle with `--observed-at 2026-09-22`. A second build in a fresh hash-locked Python environment produced byte-identical timing CSVs. This is a staged candidate, not the published database.

| Measure | Rows |
| --- | ---: |
| Canonical notices | 86,435 |
| Legal notice date missing | 20,855 |
| Legal notice date present, precision unassessed | 60,743 |
| Effective start missing | 11,572 |
| Effective start present, precision unassessed | 67,814 |
| Provisional formatted notice/start pairs | 54,224 |
| Source-supported notice/start pairs | 4,815 |
| Source-supported notice/end pairs | 1,251 |

The strict start cohort is Florida archive HTML **2,552**, South Carolina 2026 PDF **26**, reviewed New York filings **3**, and pinned Texas annual workbooks **2,234**. Strict end counts remain Florida **1,241**, South Carolina **8**, and New York **2**. The Texas effective dates are the source's listed `LayOff_Date`: an anticipated layoff/action date, not proven individual separation. The guarded Texas rule holds a key with conflicting raw rows in its version history, dates changed by an upstream year correction, and unmatched source rows; it preserves 812 negative intervals as explicit review signals.

Compared with v5, the Washington and Minnesota role corrections move **1,782** dates out of canonical legal notice: 1,459 Washington agency receipt dates and 323 Minnesota receipt or layoff-fallback dates. Their 1,777 prior provisional pairs leave the timing cohort. The Texas rule does not add new formatted pairs; it promotes 2,234 already paired rows to the source-supported stratum after workbook/raw/candidate checks. A day-formatted pair is still provisional unless its source role and precision are verified.

The timing cohorts measure notice to a reported start or end of a planned action. They do not, without filing-level evidence, measure when each employee received notice or lost work. Sodexo's nine days are notice to closing/action start, while its filing describes distinct employee separation phases. Effective end stays blank for phase lists and for any source that does not establish a continuous range.

These counts measure evidence coverage, not a database-wide date error rate. Most states still lack audited source-role metadata; Massachusetts, Nevada, and Kentucky have [unresolved receipt-date evidence](next-receipt-date-source-review-2026-09-23.md). New Jersey's posting-month rows have unknown legal notice dates despite 63 source-supported effective ranges. The `date_eligibility_by_notice.csv` output records each row's eligibility reason and source rule.
