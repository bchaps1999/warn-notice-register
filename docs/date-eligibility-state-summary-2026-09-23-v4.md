# Date eligibility by state: v4 source-only checkpoint

The [state table](date-eligibility-state-summary-2026-09-23-v4.csv) summarizes
the read-only timing gate on isolated candidate
`/private/tmp/warn-sc-date-v4-a-20260923.sqlite`. It is tied to the pinned
First Transit source bundle and fixed `--observed-at 2026-09-22`, before any
Sodexo identity decision. The state totals sum to 86,436 candidate notices.

| Measure | Rows |
| --- | ---: |
| Legal notice date missing | 19,073 |
| Legal notice date present, precision unassessed | 64,770 |
| Effective start missing | 11,574 |
| Effective start present, precision unassessed | 70,048 |
| Provisional formatted notice/start pairs | 56,000 |
| Source-supported notice/start pairs | 2,580 |
| Source-supported notice/end pairs | 1,251 |

The strict start cohort is Florida archived HTML **2,552**, South Carolina's
pinned 2026 report **26**, and two reviewed New York filings. The strict end
cohort is those same sources **1,241**, **8**, and **2**, respectively. This is
an evidence-coverage denominator, not a database-wide date accuracy estimate.
Many formatted date strings are not yet source-role assessed. New Jersey's
2,327 admitted rows have unknown legal notice days and contribute zero strict
timing rows despite 63 ordered effective ranges.

`notice_precision_unassessed` and `effective_start_precision_unassessed` count
present dates whose explicit precision is null. They exclude missing dates.
The provisional cohort accepts a day-formatted pair with null notice
precision; it does not assert source accuracy. A strict interval requires
source-supported day precision and basis for both relevant date roles and no
unresolved date/range review. Negative intervals remain visible for review.
The underlying per-notice reasons and source-rule IDs are in the generated
`date_eligibility_by_notice.csv` and cohort files from
`python -m warnlive.verify.timing` on the named candidate.
