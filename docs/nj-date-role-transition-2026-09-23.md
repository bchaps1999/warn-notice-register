# New Jersey posting-month role correction

New Jersey's frozen raw CSV has `Month Posted` and `Effective Date`; it does not
have a notice-date field. The prior transformer inferred a year for the posting
month from the effective date, stored its first day as `notice_date`, and used
that invented date in the dedupe key. This confused posting with legal notice
and could also merge separate rows when employer, city, and inferred month
coincided. The [cross-state date audit](date-candidate-audit-2026-09-23.md)
already excluded the 2,267 marked month-precision rows from exact-day timing;
the role error still appeared in the canonical database and exports.

The staged source-first correction leaves NJ `notice_date` null and preserves
the raw posting month in `source_details.dates` with role `posting_month`,
source field/text, parsed month if valid, null year/date, month precision,
and reported basis. A year is not inferred. Exact raw table rows receive a
content-derived **observation anchor** (`NJ:raw-row:<sha256>`) before deduping.
That anchor is not a state filing ID or proof that two changed rows represent
different real-world events; changed rows require correspondence review.

The [frozen correspondence report](nj-source-correspondence-2026-09-23.md)
accounts for both current and historical NJ snapshots before this change.
The first isolated corrected candidate has 2,315 NJ records: 2,378 current
prepared rows less 13 normalization failures and 50 occurrences of repeated
identical rows with no filing ID. All 2,315 records have distinct observation
anchors and blank canonical notice dates; 2,306 have an effective start.
The 50 repeated current rows are in the exception ledger. In the older raw
snapshot, 2,302 already-represented parsed rows are skipped, 51 rows without
an admitted current identity are held, and two fail parsing. BLN NJ rows are
held as source-identity unresolved until a source correspondence justifies
admission. All source-row accounting fields in the rebuild report sum to zero
unaccounted rows.

Relative to the prior source-only candidate, the corrected candidate has 36
more notices, 5,140 more reported workers, 23 fewer versions, and 53 fewer
links. Every non-NJ semantic notice in the two candidates has identical
content. The NJ change is a fresh-rebuild identity interpretation, not an
in-place update of published IDs or a claim that the new notice count is
ground truth. The strict timing cohorts remain 2,554 notice-to-start rows and
1,243 notice-to-end rows, all from Florida archived tables and two reviewed
New York filings. All 2,315 NJ records are excluded with
`notice_date_missing`; the provisional cohort is also unchanged because the
old month placeholders were already excluded.

The first corrected candidate is
`/private/tmp/warn-nj-role-a-20260923.sqlite`; its report is
`/private/tmp/warn-nj-role-a-20260923.report.json`. It has 86,424 notices,
92,471 versions, 9,440 links, 9,003,900 workers, integrity `ok`, and zero
foreign-key errors. The 39,072-row exception ledger has SHA-256
`d618fa3f99aa111193ddba2b4c703b3f757db9563330b1602f939abf2e61bdd7`.
These are an interim checkpoint. Date range parsing for NJ's 2026 source
rows is under review; after that code is fixed, rerun the entire frozen bundle
twice and compare the new database, CSV, and site outputs.

Remaining release work: review repeated rows and historical/BLN
correspondences, distinguish amendments and multi-site events, produce an
old-key/version to new-observation disposition table, verify effective-date
range/list semantics, and inspect site display for the yearless posting
month. Unknown notice date is the honest value until a filing provides it.

## Later source-only range checkpoint and unresolved event keys

After the first checkpoint, the NJ adapter gained conservative effective-date
pair handling. The current fixed-code candidate at
`/private/tmp/warn-nj-role-v3-a-20260923.sqlite` has 2,327 NJ observations,
all with null legal notice date. Of these, 2,318 have an effective start and 63
have a reported ordered range end. An `and` pair is treated as a phase list,
not a continuous range; malformed or reversed pairs remain held. The complete
candidate has 86,436 notices, 92,483 versions, 9,440 links, and 9,006,043
workers, with SQLite integrity `ok` and zero foreign-key errors. This is still
an isolated candidate.

The read-only old-to-new disposition accounts for 2,378 current NJ row
occurrences as 2,351 distinct raw rows: 2,327 admitted observations, 23
distinct repeated-row groups held (50 occurrences), and one parse failure.
Every non-admitted occurrence now requires an exception in the **current**
rebuild ledger; old parser failures are only historical diagnostics. The
transition report also produces a [41-group fanout review sheet](nj-old-key-fanout-review-2026-09-23.csv): 41 old notice IDs nominate two or more new raw observations, 94 new observations in all. The largest group is seven Novartis East Hanover rows on one reported effective date with seven different worker counts. This does not establish seven separate filings or a single amended filing. It is a priority source-document review before release; no automatic merge was made.

The corrected candidate's strict timing cohorts still contain 2,554
notice-to-start and 1,243 notice-to-end rows, all from the Florida archive
rule and the two reviewed New York filings. NJ contributes none because a
posting month is not a legal notice day. A second fixed-code replay matches
the active notice, version, and link fingerprints and the 39,059-row
exception ledger (SHA-256 `f411c892848372b36f25bf319351c12c6d21bbc442bea0fa314f909a95fd71bd`).
The two 49-file CSV trees match (SHA-256
`f01102f6749d1f28b35c2983f3509ee401d64dea60312d7f56111b7850601391`),
as do the two 568-file site trees at `--as-of 2026-09-22` (SHA-256
`c2b2e99534ac9330cf3a549ae014493dd77ea6481f7cd527736a8da59f0282a3`).
The earlier checkpoint numbers above remain for traceability and should not
be combined with these newer counts.
