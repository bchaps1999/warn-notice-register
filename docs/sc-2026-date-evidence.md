# South Carolina 2026 report date roles

The pinned `cache/sc/2026.pdf` in the reviewed source bundle has SHA-256
`a4e3f4303ed4c9ef1fc3637c7597f016a425bb53ff80a73ea4951964c5b17fc3`.
Its first page labels separate `Notice Date` and `Layoff/Closure Date`
columns. Eight of the 26 report rows show an ordered pair in the latter
column. The report heading's `Start Date: 1/1/2026` and `End Date:
7/10/2026` describe the **report coverage window**, not each employer's
layoff range. Page two is a county summary, not additional notice records.

The cached parser produces 26 rows from the first-page table. A focused test
re-parses the pinned PDF through `cached_csv`, normalizes the result, and
checks the International Paper February 18 notice and May 1–December 31
effective range. All 26 normalized rows have source-matched day-level notice
and effective-start cells; eight have source-matched day-level effective ends.
The source rule `sc_2026_report_notice_layoff_v1` assigns `day`/`reported`
only when the selected canonical day equals the parsed raw cell and the row
identifies page one of `sc/2026.pdf`. It does not apply to older South Carolina
tables, whose `legacy_date` may be a projected effective date without a legal
notice day.

This verifies the **table field roles** and a source-stratified extraction,
not the individual employer filings or whether planned separations occurred
on the reported dates. The PDF is a capture through July 10,
2026; it is not complete full-year coverage. A later report or revised filing
needs its own source hash, correspondence, and rule review. The staged
source-only candidate will be replayed twice after this metadata rule before
its timing cohort is updated. No LLM proposal is involved.
