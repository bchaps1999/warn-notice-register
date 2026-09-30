# Nebraska report separation and keys, Nevada 2021 transcription, Mississippi date spills (2026-09-30)

Branch `data-quality-fixes`, base commit `14989bf`, uncommitted working tree
(several agents editing). Nothing here is a release: it prepares source
evidence, code and transition maps for v1.2. Published database used for
comparisons: `data/warn.sql.gz` at `14989bf` (sha256 `8471c32a…5828`),
restored to a scratch SQLite file (sha256 `2a0432fc…1e5ad`).

## 1. Nebraska: WARN report vs layoff/closure report

**Problem.** warn-scraper's `ne.py` reads NDOL's current WARN page and, for
2010–2019, appends two year reports into one CSV:
`LayoffServices/WARNReportData/?year=` (the WARN report) and
`LayoffServices/LayoffAndClosureReportData/?year=` (NDOL's general
layoff/closure report: bank branches, restaurants, small closures). All 933
published NE notices share one `source_url` and carry no `source_details`, so
the register could not tell them apart.

**Decided policy.** Tag every row with `source_details.source_report`
(`warn_report` | `layoff_closure_report`), admit WARN report rows, hold
layoff/closure rows as `ne_layoff_closure_report_not_warn` (source
observations, not notices). Where a layoff/closure row has the same folded
employer name and the same parsed date as a WARN report row, only the WARN
notice is kept and the held row records the match
(`ndol_matched_warn_row = warn_report:<year>:<page row>`). The current WARN
page and its archived 2020–2022 capture are tagged `warn_report`, with
`ndol_source_page` naming the page.

**Captures.** All 20 year reports and the current WARN page were fetched
2026-09-30 01:40–01:42 UTC with a url/retrieved_at/sha256 sidecar each, and
copied with the pinned 2020–2022 Wayback capture into
`data/source_snapshots/2026-09-30-ne-ndol-reports/` (layout `archives/ne/…`,
matching the collector cache; `manifest.json` sha256 `982557d2…0a54d`). The
collector output from those captures is `ne.csv` in the same directory
(sha256 `babe7c48…f612`, 960 rows). Pages print "Events as of <today>", so
their bytes change daily; the sidecar hash pins what was read. An empty year
returns a two-row "No events to display." table (2010 layoff/closure), which
the parser accepts as zero rows; any other shape is an error.

| Year | WARN report rows | Layoff/closure rows | Layoff/closure rows matching a WARN row |
|---|---:|---:|---:|
| 2010 | 6 | 0 | 0 |
| 2011 | 7 | 40 | 0 |
| 2012 | 9 | 44 | 0 |
| 2013 | 7 | 40 | 0 |
| 2014 | 7 | 36 | 2 (HDA/N-Store 2014-02-03, Nationstar 2014-10-14) |
| 2015 | 15 | 102 | 1 (IAC Acoustics 2015-11-17) |
| 2016 | 5 | 110 | 0 |
| 2017 | 8 | 126 | 0 |
| 2018 | 7 | 133 | 0 |
| 2019 | 17 | 194 | 0 |
| **Total** | **88** | **825** | **3** |

The current WARN page had 47 rows (2023-03-01 to 2026-08-26; one, Fortrex
0129 Madison 2026-08-26, is not yet published). With the hook below, the full
CSV normalizes to 960 raw rows: 135 records, 825 held, 0 parse failures.

Layoff/closure rows are not all small: 108 report 50+ workers and 37 report
100+ (Hostess 2012, Toys-R-Us 2018, Shopko 2019). They are held because they
are not in NDOL's WARN report, not because they are shown to be non-WARN
events. That is what the observation export should say.

**Published notices affected** (`data/review/ne-key-transition-2026-09-30.csv`,
summary `…summary.json` with `retired_dedupe_keys`):

| Year | keep | rekey | retire |
|---|---:|---:|---:|
| 2010 | 6 | | |
| 2011 | 7 | | 40 |
| 2012 | 8 | | 41 |
| 2013 | 6 | 1 | 40 |
| 2014 | 7 | | 33 |
| 2015 | 13 | | 100 |
| 2016 | 5 | | 109 |
| 2017 | 8 | | 122 |
| 2018 | 7 | | 130 |
| 2019 | 16 | | 188 |
| 2023–2026 | | 46 | |
| **Total** | **83** | **47** | **803** |

No published NE notice was left unmatched. Pre-2020 published NE falls from
887 to 84. Three kept notices also had a held layoff/closure row with the same
key (the three matches above).

## 2. Nebraska: location and keys

`normalize/custom/ne.py` maps location to `City` when present, else the filed
`Location` cell. The year reports fill `City` with the city and `Location`
with a site description ("Oakview Mall", "Headquarter"), so rows with a City
keep their location and key. Rows without a City change key: all 46
published 2023+ page rows, one 2013 WARN report row (American Suzuki, Butler
County), and the 29 unpublished 2020–2022 backfill rows. The two Hayneedle
2020-01-23 sites now separate ("Chalco Valley Parkway - Omaha", "West Dodge
Road - Omaha") and `fetch_ne_dol` returns all 29 rows (it held both before).
Each row of the transition CSV gives old key, new key, source rows and
evidence. Backfill rows appear as `backfill_new_key` with their old City-based
key; both Hayneedle rows show the same old key.

**Not resolved.** Four same-day multi-row WARN report filings still share a
key because they share a City: Schreiber Foods 2012-08-23 (Ravenna, 21 and
43 workers, identical Location), Michael Foods 2015-05-21 (Wakefield, Farm
vs Plant Facility), Skag-Way 2015-03-23 (Grand Island, Locust vs State
Street), Hayneedle 2019-05-02 (Omaha, Chalco Valley Parkway vs West Dodge
Road). The published register folded each pair into one notice with two
versions, so the first row's count is not current (e.g. Hayneedle 2019 shows
59; the 180-worker site is only a prior version). Separating them needs a key
or location that uses the year reports' `Location` column. That changes the
published location text and keys of up to 20 WARN report rows, so it is left
for a policy decision.

## 3. Nevada 2021 transcription

- `WARN_2021.pdf`: a direct fetch on 2026-09-30 returned HTTP 403 (Akamai).
  The evidence copy is Wayback capture `20260615103757`, sha256
  `9f3dadcd…daaa5`, byte-identical to the file fetched directly from
  detr.nv.gov on 2026-07-16 (`workdir/cache/nv/`) and 2026-09-29. Word export
  of 2021-12-21: one page, one 842×387 image, no text layer.
- `2021_updated_210222.pdf`: Wayback `20240302153413`, sha256
  `662ded77…7046`, text, 6 rows.

Method: extracted the embedded image losslessly (pdfimages), ran a first
OCR pass (tesseract 5.5.0, 4× upscale), then read every cell from 4×
row-numbered crops (red `r01`–`r20` labels; grid lines at y = 58+16(n−1) in
the embedded image; nothing below r20). OCR and the transcription differ in
one cell, r07 ("A&B" in OCR, "A & B" in the image). Against the interim PDF
(r01–r04, r06, r08), dates, types, counts, cities and counties all agree. Four
employer spellings differ; the scan's text is kept, and each difference is
noted in `transcription_note`. The interim PDF's Notice Date column is not in
the scan and is not used. `transcription_basis` is `ocr+manual` for all 20
rows. `Notification` is blank because the list has no WARN/Non-WARN column;
the collector sets WARN, as it does for the other WARN-only yearly files. No
2021 row matches a published NV notice. The published Rawhide Mine row
(received 2021-12-26) comes after this list.

Evidence: `data/source_snapshots/2026-09-30-nv-2021-transcription/` holds
`manifest.json` (sha256 `be6fe4e1…242c`), both PDFs,
`images/WARN_2021-embedded-image.png`, `images/WARN_2021-rows-annotated-4x.png`,
four crops `images/WARN_2021-rows-{01-05,06-10,11-15,16-20}-4x.png`,
`ocr-tesseract-first-pass.txt` and `nv-2021-transcription.csv` (sha256
`333d7eae…85`). Independent cell-by-cell verification is pending. A correction
must be written as a new dated file, and `fetch/custom/nv.TRANSCRIPTIONS` must
then point to it.

Wiring: when a listed PDF yields no text rows and its sha256 is in
`fetch/custom/nv.TRANSCRIPTIONS`, the collector writes the transcribed rows
with `transcription_basis` and `transcription_source` (`<csv>#rNN`). The
normalizer then records `source_details.transcription` (rule
`nv_scanned_list_transcription_v1`). With the hook, the 20 rows normalize with
0 failures and 20 distinct keys.

## 4. Mississippi date-cell spills

The upstream PDF parser sometimes widens the date column. It puts the next
cell's leading words into the date cell (`"4/17/2026 Aramark"` + company
`"Services, Inc"`; `"05/11/2026 Leggett &"` + `"Platt Flooring Products"`;
`"6/15/2026 WARN – Due"` + reason). The general rule in `normalize/custom/ms.py`
works like this. When a date cell holds a date followed by text that is not
another date, the date comes from the leading token and the text goes back in
front of the next cell. The company then goes through the existing
City (County) split. On `workdir/raw/ms.csv` (148 rows, captured 2026-07-26)
this fixes 14 employers, including Aramark Services, Inc, Leggett & Platt
Flooring Products, Sun Air Products (was "Belmont (Tishomingo)") and Cooper
Lighting Solutions. With the details hook, all 148 dedupe keys are unchanged:
`legacy_key_fields` keeps the filed cells' key, the same approach as the site
split. Without the hook, only Aramark and Leggett would change key.

## Checks run

- Focused tests in the checkout: `tests/test_ne_reports.py`,
  `test_ne_dol_archive.py`, `test_nv_2021_transcription.py`,
  `test_ms_date_spill.py`, `test_custom_states.py`, `test_parse_fixes.py`,
  `test_source_bundle.py`, `test_offline_rebuild.py`. All pass except the three
  tests that need the hooks in `normalize/details.py` and
  `normalize/nonnotice.py`, which this work did not own.
- The same tests plus `test_registry`, `test_engine`,
  `test_source_type_columns`, `test_date_precision_detection`, `test_pipeline`,
  `test_cli_scrape` and `test_fetch_budget` were run on a scratch copy with the
  hooks applied: 228 passed. The only failures, 3 in `test_source_bundle`,
  came from agency snapshot directories missing from the scratch copy; those
  tests pass in the checkout.
- The full suite and any scrape or rebuild were not run.

## Nevada 2021 transcription: independent verification (2026-09-30)

A second agent, working without the transcription, re-extracted the page
image from `WARN_2021.pdf`. The image is pixel-identical to the saved
original. The agent transcribed all 20 rows itself and then compared them cell
by cell with the transcription.

Results:
- All 160 cells agree, with no disagreements and no cells unreadable.
- The row count matches the image, and no row is skipped or duplicated.
- All 10 manifest SHA-256 values match their files.
- The `interim_pdf_row` mapping agrees with the text of the interim PDF.

The per-row report is
`data/source_snapshots/2026-09-30-nv-2021-transcription/independent-verification.md`.
The source's own inconsistencies are kept as filed:
- effective dates before received dates on seven rows;
- Silverton's interim notice date later than its received date;
- the misspelling "Behavorial".
