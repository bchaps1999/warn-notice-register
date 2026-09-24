# Date eligibility by state: v9 Kentucky receipt-role checkpoint

The [state table](date-eligibility-state-summary-2026-09-23-v9.csv) summarizes the read-only timing gate on an isolated source-only candidate built from the pinned Texas annual-workbook bundle with `--observed-at 2026-09-22`. The Kentucky `date_received` field is treated as agency receipt. Its raw text and parsed day remain in source details and the old receipt-based key remains stable, but the field no longer supplies a legal `notice_date`. Kentucky `date_effective` still supplies reported effective starts and, where present, range ends. This is a source-role correction, not a claim that the employer notified workers on the receipt day.

| Measure | Rows |
| --- | ---: |
| Canonical notices | 85,954 |
| Legal notice date missing | 22,490 |
| Legal notice date present, precision unassessed | 58,627 |
| Effective start missing | 11,569 |
| Effective start present, precision unassessed | 67,336 |
| Provisional formatted notice/start pairs | 52,404 |
| Source-supported notice/start pairs | 4,815 |
| Source-supported notice/end pairs | 1,251 |

The frozen Kentucky current table has 828 rows under 806 stable keys. Its 819 ISO received days are preserved as receipt facts; seven cells are blank and two contain `N/A` or `November`. All 806 keys remain admitted with zero parse failures. They have 538 reported effective starts and 83 effective range ends, but no source-supported legal notice day from this table.

The v8 candidate also admitted 481 Kentucky-only BLN transcription keys. They represented 107,932 workers and 478 formatted notice/effective pairs, including 299 negative intervals. We have no source filing identity or notice-role definition that can safely connect these rows to the frozen state table. Clearing the current table's notice dates without a matching admission guard admitted 675 **additional** BLN notices in a rejected diagnostic run. The guarded v9 rule accounts for Kentucky BLN rows in the source exception manifest as `source_identity_unresolved`. The 481 prior admissions therefore leave the candidate, and Kentucky coverage falls from 1,287 to 806 notices and from 214,798 to 106,866 workers. This is a material, explicit source-evidence limitation; it does not establish that those 481 historical events did not occur.

The strict timing cohorts remain at 4,815 starts and 1,251 ends because the Kentucky current table never supplied source-supported legal notice days. The 52,404 formatted pairs are provisional, including dates with unassessed source roles or precision. Effective dates describe reported action timing; an effective range end is not automatically an individual employee's separation day.
