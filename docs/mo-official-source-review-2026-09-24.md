# Missouri original-source check, 2026-09-24

The [Missouri Office of Workforce Development WARN index](https://jobs.mo.gov/employer/warn)
links both annual 2019–26 tables and a **WARN Data 1997-2018** workbook under
Related Resources. The workbook link is hosted on `jobs.mo.gov`, making it a
direct agency publication rather than a third-party reconstruction. A browser
download succeeded even though direct `curl` requests returned an Imperva
challenge. The exact 553,366-byte file is pinned with SHA-256
`3e783842d6086e7f176c20d81de6906873c1bb2903126a3ceee900adece8141c`
at [the source manifest](../data/source_snapshots/mo/historical-1997-2019/manifest.json).

The workbook contains 22 fiscal-year sheets and 1,301 rows with a company
cell. Its observed agency receipt dates run from July 14, 1997 through June
27, 2019, despite the 2018 end year in the filename. `Date Rec'd` is an
agency receipt date, while `Layoff or Closing Date` is a reported action date;
neither is automatically a legal employer notice date. Headers change across
years, and the sheets lack a consistent notice ID. Several rows have complex
date or worker cells. The sheets include totals and other non-record rows,
which are excluded from the 1,301 figure.

The 2019–24 annual tables have 297 displayed rows, broken out as 26, 162,
22, 10, 37, and 40. They are pinned as line-numbered **browser-rendered text**
with URLs, byte hashes, headers, and row counts in [the annual manifest](../data/source_snapshots/mo/annual-2019-2024/manifest.json).
These captures are not raw HTTP HTML. The row counts and first/last records
were also checked in the browser. **295 annual rows are admitted**, while a
zero-affected Southwest Airlines row and an Overland Park, Kansas row are held.
The official table establishes the WARN listing; missing site and worker
cells remain null. Multi-phase, tentative, or invalid action dates remain
null instead of becoming a guessed day. `Received` is agency receipt, not a
verified legal employer notice date.

The workbook is a mixed rapid-response register. Each admitted row has
individually reviewed positive filing language in its comments and is pinned
to sheet, physical row, and raw-cell hash. **17 rows are admitted; 1,284 are
held** as unreviewed rapid-response rows. A worker threshold does not decide
filing status. Penske's comment says its original WARN action day moved
forward without giving the new day, so its canonical action day is null.
2018–19 sheet row 4, reported by a job seeker and media, remains held.

Neither source has a consistent filing ID. The projector screens employer
and reported action-day overlap with current Missouri captures and between
the two new layers. All admitted and held rows retain source pointers and
raw fields. Underlying filings or an agency records request could resolve
more of the older register, especially its ambiguous WARN mentions.

This recovery is in an isolated source-only candidate. Its replay and
exception accounting are summarized in [the expansion review](agency-mo-expansion-2026-09-24.md).
The public database and site have not been replaced.
