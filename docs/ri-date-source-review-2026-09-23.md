# Rhode Island date source review

This review uses the 126-row `raw/ri.csv` in the pinned September 23 Texas annual-workbook bundle and the isolated v9 source-only candidate. The [Rhode Island Department of Labor and Training WARN page](https://dlt.ri.gov/employers/worker-adjustment-and-retraining-notification-warn) links its WARN workbook and says the public list is updated after receipt. The frozen table separately labels `WARN Date`, `Date Received`, and `Effective Date`. The page does not define `WARN Date` as the day employees received notice. The linked workbook returned HTTP 403 during this review; the frozen table is the row-level evidence available for this checkpoint.

## Timing and source quality

- In v9, 124 of 126 active Rhode Island notices have both canonical notice and effective starts. **78** starts precede the saved `WARN Date`. This is a source-quality signal; it does not by itself establish a parser bug or justify changing years.
- In the frozen current table, 107 rows have both date columns in ISO form; 70 are negative. The other effective cells include ranges and phase lists. `Date Received` precedes `WARN Date` in 82 of these 107 pairs; for 50 of the 70 negative pairs the effective date is on or after receipt. The fields therefore cannot be assumed interchangeable.
- The row for First Student has `WARN Date` September 8, 2017, `Date Received` May 5, 2016, and `Effective Date` June 30, 2016. Homemade Real Foods has a similar one-year gap. These are literal source-table values, not values introduced by our date parser. Original filing letters are needed to decide which day corresponds to legally given notice.
- Two frozen `WARN Date` cells read `2108-10-23` and `2108-11-01`. The pinned upstream transformer explicitly corrects those to 2018 before ingestion. A separate `5/4/204` cell for ASM GLOBAL is unparseable; no complete day is inferred from it. These source corrections and failures should remain visible in provenance and denominator reporting.

## Effective ranges

The frozen table contains explicit continuous intervals alongside comma and ampersand phase lists. The current candidate retained their first effective day but no Rhode Island effective end. The narrow existing range projector now examines Rhode Island's `Effective Date` source cell. It yields **five** ascending continuous start/end pairs, including NMC's May 2 to July 1, 2024 interval and Allied Group's May 25 to June 6, 2026 interval. It leaves the eight two-or-more-day list/phase cells without scalar ends. Cells such as `1/04/16 through 8/2017` have an incomplete month endpoint and are not forced into an exact-day end. Raw text remains preserved.

The end date is a reported end of the action interval. It does not assert that every affected worker separated on that day. None of these Rhode Island rows enters the strict notice-to-end cohort because the table's `WARN Date` role and day precision have not been confirmed against original filings.

## Next evidence

Pin the original agency workbook or dated filing letters for a small stratified sample: pre-2020 negative intervals, 2020 retrospective notices, positive intervals, and explicit ranges. Reconcile `WARN Date` and `Date Received` to each document. Keep negative values and source typos as flags until their roles are established. Do not substitute `Date Received` for `WARN Date` merely to make intervals positive.
