# Louisiana official-source review and candidate overlay

The frozen [2025](../data/source_snapshots/la/2025.pdf) and
[2026](../data/source_snapshots/la/2026.pdf) Louisiana Works tables contain 38
notice rows and one rescission annotation. The row-level evidence report is
recreated with:

```bash
python -m warnlive.migrate.la_reconcile \
  --bundle data/source_snapshots/2026-09-23-la-official.tar.gz \
  --db /path/to/source-only-candidate.sqlite \
  --out /path/to/new-la-review.json
```

The source extractor now records the UPS annotation's typed 2025-09-05
rescission date, verbatim annotation quote, and target row pointer. These are
status evidence; the annotation remains outside notice counts and is not a
layoff-end date. The source-hash-bound
[`la-relationships-2026-09-23.json`](../data/review/la-relationships-2026-09-23.json)
keeps both IDEA rows and all four held SafeSource rows unresolved. Its
`87 + 454 = 541` observation is arithmetic evidence, not a filing or worker
allocation decision; the 56-worker `Second WARN` row is also explicitly held.

`source_row` uses PDF page/table-row ordinals; BLN `source_row` uses the CSV
data-row ordinal. A matching notice date and worker count proposes a candidate
for inspection, **not** a same-notice decision. These observations are not a
human-labeled gold set and must not be used as model accuracy ground truth.

## Candidate database observations

For the pre-overlay September 23 source-only replay (86,936 notices), 29 official rows
have one canonical date/worker signature, three have two, two have three, and
four have none. The two IDEA official rows share a signature, so these are
row-level buckets, not distinct-event counts. That candidate database had not
ingested the official PDFs. Running the report against two independently
rebuilt candidate databases produced the same SHA-256
(`9227981b091eca636b8eeae3d8feb944f3e6cf6633f9d3959a872141b6d579cf`).

| Official source row(s) | What the source and candidate show | Required decision |
| --- | --- | --- |
| `2025.pdf:p1:r7-8` (IDEA) | Two official rows have the same notice date, layoff date, 212 workers, and two named sites. Three canonical rows share that signature, including one malformed employer `I`. | Obtain filing evidence to decide whether the two agency rows are one event, separate filings, or a revision. Do not multiply 212 by the number of rows or assign it to both sites. |
| `2025.pdf:p2:r5-6` (UPS) | The agency explicitly says the 177-worker notice was rescinded on 2025-09-05. The candidate has two 177-worker canonical rows; one is the rescission annotation misread as an employer. | Model the rescission explicitly, then remove this annotation-as-notice artifact from published active totals without losing the evidence. |
| `2025.pdf:p2:r7,r11,r13` (SafeSource) | The agency lists a 541-worker two-address row and 87- and 454-worker facility rows with the same notice and layoff dates; 87 + 454 = 541. The candidate has two 541-worker rows, plus the facility rows. | Determine whether the 541 is an aggregate of the facilities or a separate filing. Keep worker allocation unresolved until confirmed. |
| `2025.pdf:p2:r15` (Service Companies) | One official 76-worker row corresponds to two canonical rows with differently split employer/location text. | Preserve both raw transcriptions but publish at most one confirmed event after source-row reconciliation. |
| `2025.pdf:p2:r16` (GDIT) | The agency gives no notice date but does give a 2025-11-14 layoff date and 103 workers. The BLN copy has a malformed company/location transcription; the candidate has no 103-worker counterpart. Conservative gap filling skips it because November is otherwise occupied. | Admit a source-backed unknown notice date rather than inventing one; use the agency row, not the malformed BLN fields. |
| `2026.pdf:p1:r13-15` (UPS, Mosaic, Elevance) | These later official rows have no exact date/worker BLN candidate and no canonical counterpart. Mosaic lists two sites but only one 206-worker total. | Add source-backed notices after defining site-role and total-worker semantics. Do not split Mosaic's 206 without evidence. |
| `2025.pdf:p1:r10` (Cornerstone) | The agency gives a layoff interval from 2025-07-31 to 2025-12-31; the canonical row retains only the start. | Preserve the end date and interval interpretation in the next candidate. |
| `2026.pdf:p1:r6,r12` (C2, Conduent) | The agency's address cells are in VA and NJ, respectively, even though these rows appear in Louisiana's list. | Retain the addresses but do not assume they are Louisiana worksites. |

Other source anomalies also remain visible: Smitty's 2025 notice date follows its
listed layoff date, and the 2025 table combines employer and address text in a
single cell. Neither should be silently "corrected" by parsing.

## Source-first candidate overlay

The reviewed overlay in `warnlive/migrate/la_overlay.py` applies only in
`--source-only` rebuilds of the frozen bundle. It checksum-pins both the
official PDF manifest and the BLN CSV, plus the 58 reviewed BLN-to-PDF-row
correspondences. A changed input or correspondence fails closed. Of the 38
official notice rows, it admits 31, holds both IDEA and four SafeSource rows,
and excludes the rescinded 177-worker UPS row. The rescission annotation and
every held/excluded BLN transcription remain in the exception ledger. One
superseded BLN transcription stays in its original superseded category.

Run an isolated candidate without touching `data/warn.sqlite` or exports:

```bash
python -m warnlive.migrate.offline_rebuild \
  --bundle data/source_snapshots/2026-09-23-la-official.tar.gz \
  --db /path/to/new-source-only-candidate.sqlite \
  --observed-at 2026-09-22 --source-only \
  --report /path/to/new-source-only-report.json
```

The first overlay replay yielded 86,926 notices nationally, including 613
Louisiana notices and 83,991 Louisiana workers. The previous source-only
candidate had 623 Louisiana notices and 86,387 workers; those differences
are **not** a claim that the older counts were true or that all removed rows
were duplicates. The official rows contribute 5,178 workers. One BLN-only
2025 Blue Cross Blue Shield row (202 workers, notice date after effective
date) is now in the exception queue: it does not appear in the annual PDF,
and the agency's other August notices make that month ineligible for the
conservative BLN gap-fill. It is not suppressed by the reviewed PDF mapping.
The remaining Louisiana history is retained by the ordinary backfill rules.

The overlay preserves GDIT's missing notice date, Cornerstone's layoff end
date, and all three later 2026 notices. It keeps addresses in source details
with unverified roles and leaves scalar locations unset, including C2's VA
and Conduent's NJ addresses. Mosaic's two sites and one unallocated 206-worker
total are explicit; no site receives an invented headcount. No LLM calls are
part of this replay.

## Next gate

Review the original filings where available for IDEA and SafeSource. Revisit
the candidate's active-only handling of rescinded notices and its unknown-date
representation before publication. Compare its exception queue against filing-level
evidence, including the BLN-only Blue Cross row. No model should auto-merge
the unresolved rows; a held-out model
evaluation needs independently reviewed labels first.

The public Louisiana Works [WARN resources page](https://www.laworks.net/downloads/downloads_wfd.asp)
links the annual summary PDFs, but our September 22 search did not locate
individual IDEA or SafeSource WARN letters there. [IDEA's Louisiana page](https://ideapublicschools.org/states/louisiana/)
confirms the two named campuses closed, and [SafeSource's contact page](https://www.safesourcedirect.com/contact)
identifies its two Broussard facilities. Neither resolves whether the repeated
summary rows are distinct filings or how workers were allocated. Their
relationships remain open pending filing-level evidence or an explicit,
reviewed curation decision.
