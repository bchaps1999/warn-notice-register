# Date eligibility by state: v5 source-only checkpoint

The [state table](date-eligibility-state-summary-2026-09-23-v5.csv) summarizes
the read-only timing gate on isolated candidate
`/private/tmp/warn-sodexo-v5-a-20260923.sqlite`, built from the pinned Sodexo
source bundle with `--observed-at 2026-09-22`. The state totals sum to 86,435
candidate notices. This is a staged checkpoint, not the published database.

| Measure | Rows |
| --- | ---: |
| Legal notice date missing | 19,073 |
| Legal notice date present, precision unassessed | 64,768 |
| Effective start missing | 11,572 |
| Effective start present, precision unassessed | 70,048 |
| Provisional formatted notice/start pairs | 56,001 |
| Source-supported notice/start pairs | 2,581 |
| Source-supported notice/end pairs | 1,251 |

The source-supported start cohort comprises Florida archived HTML **2,552**,
South Carolina's pinned 2026 report **26**, and three reviewed New York
filings. The end cohort comprises **1,241**, **8**, and **2**, respectively.
The new New York row is Sodexo: nine days from stated notice to reported
closing/action start. The filing describes frontline separations as occurring
**on or about** that day and management separations on July 28. Therefore the
nine-day result must not be represented as an exact notice-to-employee-layoff
interval. The strict cohort CSV now exposes `effective_date_role` and
`employee_separation_phases` from reviewed provenance so analysis can distinguish
that outcome.

These are evidence-coverage denominators, not database-wide date accuracy
estimates. Many formatted dates still lack an assessed source role. New
Jersey's 2,327 admitted rows have unknown legal notice days and contribute
zero strict timing rows despite 63 ordered effective ranges.

`notice_precision_unassessed` and `effective_start_precision_unassessed` count
present dates whose explicit precision is null. They exclude missing dates.
The provisional cohort accepts a day-formatted pair with null notice precision;
it does not assert source accuracy. Strict eligibility requires source-supported
day precision and basis for both relevant date roles and no unresolved
date/range review. Negative intervals remain visible for review. The generated
`date_eligibility_by_notice.csv` and cohort files record the per-notice reasons
and source-rule IDs.
