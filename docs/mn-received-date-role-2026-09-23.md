# Minnesota WARN receipt and layoff fallback correction

Minnesota DEED's [Plant Closings/Mass Layoffs/WARN report](https://mn.gov/deed/assets/plant-closing-mass-layoff-warn-report-2024_tcm1045-663638.pdf)
lists `Layoff Start` and `WARN Received` as separate columns. The report is
prepared by the State Rapid Response Team and includes events marked `WARN
Act` yes and no. `WARN Received` denotes the team's receipt of a WARN, not a
source-labeled date on which the employer notified workers. The [DEED layoff
resources page](https://mn.gov/deed/business/layoff-resources/) separately
identifies WARN notices received from employers.

The project's prior Minnesota adapter stored `WARN Received` in canonical
`notice_date` when present and otherwise fell back to `Layoff Start`. The
fallback put a planned layoff day in the notice field. The raw source has
1,125 rows; 272 `WARN Received` cells are blank and many others contain
`-` or other text that does not parse as a day. The v5 candidate has 969
Minnesota notices, of which 323 have a nonnull canonical `notice_date` and
318 entered the provisional formatted notice/start cohort. None has a
source-supported legal employer notice date on the basis of these two
columns alone.

The `mn_warn_received_role_v1` normalization rule now clears canonical
`notice_date`, preserves the raw receipt cell as an `agency_received` source
fact, and records when the old adapter used `Layoff Start` as a missing-receipt
fallback. It keeps the prior internal key date in provenance and the dedupe
key calculation so this role correction does not collapse additional rows.
On the frozen raw CSV, normalization produced 1,125 records, zero parse
failures, and the same 969 distinct keys as the v5 candidate; every active
old source ID still appears in the normalized set. The 156 additional raw
observations sharing those keys remain an identity/version review matter.

The report's `WARN Act` field often says `NO`; that source classification
must be accounted for separately before labeling every Minnesota Rapid
Response row a legal WARN filing. This date-role correction does not make
that event-scope decision. It also does not assert actual employee separation
from `Layoff Start`. The guarded v6 replay removes all 318 Minnesota rows from
provisional notice-to-effective timing while retaining the state count of 969
notices. Strict timing remains unchanged because these rows had no explicit
day/reported notice metadata.

The guarded combined diagnostic replay retained all 969 Minnesota notices and
removed the 318 prior provisional timing pairs. It also removed 11 Minnesota
`notice_links` whose matching rule had relied on the misclassified date. Those
links remain in the [WA/MN link review sheet](wa-mn-legacy-link-review-2026-09-23.csv)
for source-level identity review; the role correction does not itself prove
that any linked filings are unrelated. Final v6 replay and export checks are
recorded in the [progress log](rebuild-progress.md).

V6 has two more Minnesota observation versions than v5, with no change in
Minnesota notice or worker totals. The extra versions are distinct frozen raw
rows for `3M- HQ 2023` and `Packers Sanitation 2023` under their existing keys;
their source status/type and date text differ. The role-aware source details
retain those distinctions. Whether each pair reflects a revision or duplicate
agency listing remains an event-identity question.
