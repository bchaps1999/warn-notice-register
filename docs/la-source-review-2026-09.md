# Louisiana official-source review (provisional)

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

`source_row` uses PDF page/table-row ordinals; BLN `source_row` uses the CSV
data-row ordinal. A matching notice date and worker count proposes a candidate
for inspection, **not** a same-notice decision. These observations are not a
human-labeled gold set and must not be used as model accuracy ground truth.

## Candidate database observations

For the September 23 source-only replay (86,936 notices), 29 official rows
have one canonical date/worker signature, three have two, two have three, and
four have none. The two IDEA official rows share a signature, so these are
row-level buckets, not distinct-event counts. The candidate database has not
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

## Next gate

Review the original filings where available for IDEA and SafeSource. Define
the canonical handling of rescinded notices and unknown notice dates. Then
apply a versioned, source-row-linked Louisiana curation manifest to a fresh
candidate, rerun coverage and worker-total checks, and compare against this
read-only report. No model should auto-merge these rows; a held-out model
evaluation needs independently reviewed labels first.

The public Louisiana Works [WARN resources page](https://www.laworks.net/downloads/downloads_wfd.asp)
links the annual summary PDFs, but our September 22 search did not locate
individual IDEA or SafeSource WARN letters there. [IDEA's Louisiana page](https://ideapublicschools.org/states/louisiana/)
confirms the two named campuses closed, and [SafeSource's contact page](https://www.safesourcedirect.com/contact)
identifies its two Broussard facilities. Neither resolves whether the repeated
summary rows are distinct filings or how workers were allocated. Their
relationships remain open pending filing-level evidence or an explicit,
reviewed curation decision.
