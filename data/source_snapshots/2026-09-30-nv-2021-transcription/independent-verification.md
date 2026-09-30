# Independent verification: Nevada 2021 WARN transcription

Verified 2026-09-29 by an independent agent. Evidence: `data/source_snapshots/2026-09-30-nv-2021-transcription/`. No evidence or repo files were edited.

## Method

1. I re-extracted the page image from `WARN_2021.pdf` with `pdfimages -png`. It is pixel-identical to `images/WARN_2021-embedded-image.png` (PIL difference bbox = None, 842x387). The PDF has one page, one image and no text layer (`pdftotext` output is 1 byte).
2. I read the embedded image at 1x and made my own 3x LANCZOS crops (`crop_00/01/10/11.png` in this folder), in four quadrants: rows 1-10 and 11-20, dates/type/count columns and employer/city/county columns. I transcribed every row from those crops into `my-reading.csv` before opening the CSV.
3. I checked that the red r01-r20 labels in `images/WARN_2021-rows-annotated-4x.png` run in order against the 20 data rows. The row below r20 is empty.
4. I compared the two transcriptions cell by cell with a script, using exact string equality and checking for stray whitespace.

## Row count

The image has 20 data rows; the CSV has 20 rows, r01-r20, each once and in order. None are skipped or duplicated. r18 and r19 (Central Freight Lines) are two separate image rows that differ by city and county. r10 and r11 (ART Asset Adjusters) likewise differ by count, city and county.

## Result

- 7 image columns x 20 rows = **140 cells: 140 agree, 0 disagree.**
- Notification column: blank in all 20 rows. The image has no WARN/Non-WARN column, so blank is correct (20/20 agree).
- Total 160/160 cells agree.

## Disagreements

None.

## Cells not readable with confidence

None. The embedded image is a clean, lossless render of a Word/Excel table and every glyph is unambiguous at 3x. These points were confirmed deliberately:
- r07 `A & B` has spaces around the ampersand (tesseract read `A&B`).
- r04 `Harrah's` has an apostrophe.
- r17 `Behavorial` is misspelled in the source and was transcribed as filed.
- r13 `T&T BLV - Buca di Beppo` has no spaces inside T&T and spaced hyphens.
- r02-r04 `Wyndham Vacation-...` have unspaced hyphens.
- r05 `Knight` is singular.
- r02 and r04 effective dates of 12/4/2020, and r03's 12/18/2020, fall before their received dates. The source shows them that way.
- r18 and r19 effective date 12/13/2021 falls before received date 12/16/2021. The source shows it that way.

## Metadata columns

- `transcription_basis`: `ocr+manual` in every row, consistent with the manifest.
- `interim_pdf_row`: checked against `pdftotext -layout 2021_updated_210222.pdf`. interim rows 1-6 map to r01, r02, r03, r04, r06 and r08. The mapping is correct, and r05 is correctly unmapped: Vegas Golden Knight does not appear in the interim list.
- `transcription_note`: the four employer-spelling notes (r02, r03, r04, r08) and the r07 OCR note match the interim PDF and OCR text. The r08 note says the interim Notice Date is 4/16/2021, which the interim PDF confirms. That date falls after the 2/10/2021 received date; this reflects the source and is not a transcription error.

## Hash check

`shasum -a 256` on all 10 files listed in `manifest.json`: **all 10 match** (both PDFs, the six PNGs, the OCR text and the CSV). All file sizes also match the ls output.

## Per-row table

| row | field | CSV value | my reading | agree |
|---|---|---|---|---|
| r01 | Received Date | 1/12/2021 | 1/12/2021 | yes |
| r01 | Effective Date | 3/31/2021 | 3/31/2021 | yes |
| r01 | Type | Closure | Closure | yes |
| r01 | Affected Total | 33 | 33 | yes |
| r01 | Employer | Food Source | Food Source | yes |
| r01 | City | Reno | Reno | yes |
| r01 | County | Washoe | Washoe | yes |
| r01 | Notification |  | (no such column in image; blank) | yes |
| r02 | Received Date | 1/14/2021 | 1/14/2021 | yes |
| r02 | Effective Date | 12/4/2020 | 12/4/2020 | yes |
| r02 | Type | Layoff | Layoff | yes |
| r02 | Affected Total | 2 | 2 | yes |
| r02 | Employer | Wyndham Vacation-Desert Blue | Wyndham Vacation-Desert Blue | yes |
| r02 | City | Las Vegas | Las Vegas | yes |
| r02 | County | Clark | Clark | yes |
| r02 | Notification |  | (no such column in image; blank) | yes |
| r03 | Received Date | 1/14/2021 | 1/14/2021 | yes |
| r03 | Effective Date | 12/18/2020 | 12/18/2020 | yes |
| r03 | Type | Layoff | Layoff | yes |
| r03 | Affected Total | 4 | 4 | yes |
| r03 | Employer | Wyndham Vacation-Grand Desert | Wyndham Vacation-Grand Desert | yes |
| r03 | City | Las Vegas | Las Vegas | yes |
| r03 | County | Clark | Clark | yes |
| r03 | Notification |  | (no such column in image; blank) | yes |
| r04 | Received Date | 1/14/2021 | 1/14/2021 | yes |
| r04 | Effective Date | 12/4/2020 | 12/4/2020 | yes |
| r04 | Type | Layoff | Layoff | yes |
| r04 | Affected Total | 23 | 23 | yes |
| r04 | Employer | Wyndham Vacation-Harrah's | Wyndham Vacation-Harrah's | yes |
| r04 | City | Las Vegas | Las Vegas | yes |
| r04 | County | Clark | Clark | yes |
| r04 | Notification |  | (no such column in image; blank) | yes |
| r05 | Received Date | 1/26/2021 | 1/26/2021 | yes |
| r05 | Effective Date | 1/7/2021 | 1/7/2021 | yes |
| r05 | Type | Layoff | Layoff | yes |
| r05 | Affected Total | 13 | 13 | yes |
| r05 | Employer | Vegas Golden Knight/Henderson Silver | Vegas Golden Knight/Henderson Silver | yes |
| r05 | City | Las Vegas | Las Vegas | yes |
| r05 | County | Clark | Clark | yes |
| r05 | Notification |  | (no such column in image; blank) | yes |
| r06 | Received Date | 1/26/2021 | 1/26/2021 | yes |
| r06 | Effective Date | 4/1/2021 | 4/1/2021 | yes |
| r06 | Type | Closure | Closure | yes |
| r06 | Affected Total | 242 | 242 | yes |
| r06 | Employer | Sykes Corporation | Sykes Corporation | yes |
| r06 | City | Las Vegas | Las Vegas | yes |
| r06 | County | Clark | Clark | yes |
| r06 | Notification |  | (no such column in image; blank) | yes |
| r07 | Received Date | 2/3/2021 | 2/3/2021 | yes |
| r07 | Effective Date | 1/27/2021 | 1/27/2021 | yes |
| r07 | Type | Layoff | Layoff | yes |
| r07 | Affected Total | 10 | 10 | yes |
| r07 | Employer | A & B Precision Metals, Inc. | A & B Precision Metals, Inc. | yes |
| r07 | City | Reno | Reno | yes |
| r07 | County | Washoe | Washoe | yes |
| r07 | Notification |  | (no such column in image; blank) | yes |
| r08 | Received Date | 2/10/2021 | 2/10/2021 | yes |
| r08 | Effective Date | 2/12/2021 | 2/12/2021 | yes |
| r08 | Type | Layoff | Layoff | yes |
| r08 | Affected Total | 45 | 45 | yes |
| r08 | Employer | Silverton Casino | Silverton Casino | yes |
| r08 | City | Las Vegas | Las Vegas | yes |
| r08 | County | Clark | Clark | yes |
| r08 | Notification |  | (no such column in image; blank) | yes |
| r09 | Received Date | 2/24/2021 | 2/24/2021 | yes |
| r09 | Effective Date | 3/1/2021 | 3/1/2021 | yes |
| r09 | Type | Layoff | Layoff | yes |
| r09 | Affected Total | 10 | 10 | yes |
| r09 | Employer | Fry's Electronics, Inc. | Fry's Electronics, Inc. | yes |
| r09 | City | Las Vegas | Las Vegas | yes |
| r09 | County | Clark | Clark | yes |
| r09 | Notification |  | (no such column in image; blank) | yes |
| r10 | Received Date | 4/26/2021 | 4/26/2021 | yes |
| r10 | Effective Date | 4/28/2021 | 4/28/2021 | yes |
| r10 | Type | Layoff | Layoff | yes |
| r10 | Affected Total | 7 | 7 | yes |
| r10 | Employer | ART Asset Adjusters | ART Asset Adjusters | yes |
| r10 | City | Las Vegas | Las Vegas | yes |
| r10 | County | Clark | Clark | yes |
| r10 | Notification |  | (no such column in image; blank) | yes |
| r11 | Received Date | 4/26/2021 | 4/26/2021 | yes |
| r11 | Effective Date | 4/28/2021 | 4/28/2021 | yes |
| r11 | Type | Layoff | Layoff | yes |
| r11 | Affected Total | 2 | 2 | yes |
| r11 | Employer | ART Asset Adjusters | ART Asset Adjusters | yes |
| r11 | City | Sparks | Sparks | yes |
| r11 | County | Washoe | Washoe | yes |
| r11 | Notification |  | (no such column in image; blank) | yes |
| r12 | Received Date | 5/24/2021 | 5/24/2021 | yes |
| r12 | Effective Date | 6/15/2021 | 6/15/2021 | yes |
| r12 | Type | Closure | Closure | yes |
| r12 | Affected Total | 99 | 99 | yes |
| r12 | Employer | Aerion Supersonic LLC | Aerion Supersonic LLC | yes |
| r12 | City | Reno | Reno | yes |
| r12 | County | Washoe | Washoe | yes |
| r12 | Notification |  | (no such column in image; blank) | yes |
| r13 | Received Date | 7/29/2021 | 7/29/2021 | yes |
| r13 | Effective Date | 7/31/2021 | 7/31/2021 | yes |
| r13 | Type | Layoff | Layoff | yes |
| r13 | Affected Total | 95 | 95 | yes |
| r13 | Employer | T&T BLV - Buca di Beppo Italian Restaurant | T&T BLV - Buca di Beppo Italian Restaurant | yes |
| r13 | City | Las Vegas | Las Vegas | yes |
| r13 | County | Clark | Clark | yes |
| r13 | Notification |  | (no such column in image; blank) | yes |
| r14 | Received Date | 8/1/2021 | 8/1/2021 | yes |
| r14 | Effective Date | 8/24/2021 | 8/24/2021 | yes |
| r14 | Type | Layoff | Layoff | yes |
| r14 | Affected Total | 100 | 100 | yes |
| r14 | Employer | Southern NV Culinary & Bartenders | Southern NV Culinary & Bartenders | yes |
| r14 | City | Las Vegas | Las Vegas | yes |
| r14 | County | Clark | Clark | yes |
| r14 | Notification |  | (no such column in image; blank) | yes |
| r15 | Received Date | 9/1/2021 | 9/1/2021 | yes |
| r15 | Effective Date | 10/1/2021 | 10/1/2021 | yes |
| r15 | Type | Layoff | Layoff | yes |
| r15 | Affected Total | 176 | 176 | yes |
| r15 | Employer | Renown | Renown | yes |
| r15 | City | Reno | Reno | yes |
| r15 | County | Washoe | Washoe | yes |
| r15 | Notification |  | (no such column in image; blank) | yes |
| r16 | Received Date | 11/10/2021 | 11/10/2021 | yes |
| r16 | Effective Date | 11/10/2021 | 11/10/2021 | yes |
| r16 | Type | Layoff | Layoff | yes |
| r16 | Affected Total | 60 | 60 | yes |
| r16 | Employer | Hycroft Mining | Hycroft Mining | yes |
| r16 | City | Winnemucca | Winnemucca | yes |
| r16 | County | Humboldt | Humboldt | yes |
| r16 | Notification |  | (no such column in image; blank) | yes |
| r17 | Received Date | 11/22/2021 | 11/22/2021 | yes |
| r17 | Effective Date | 12/20/2021 | 12/20/2021 | yes |
| r17 | Type | Closure | Closure | yes |
| r17 | Affected Total | 116 | 116 | yes |
| r17 | Employer | West Hills Behavorial Hospital | West Hills Behavorial Hospital | yes |
| r17 | City | Reno | Reno | yes |
| r17 | County | Washoe | Washoe | yes |
| r17 | Notification |  | (no such column in image; blank) | yes |
| r18 | Received Date | 12/16/2021 | 12/16/2021 | yes |
| r18 | Effective Date | 12/13/2021 | 12/13/2021 | yes |
| r18 | Type | Closure | Closure | yes |
| r18 | Affected Total | 50 | 50 | yes |
| r18 | Employer | Central Freight Lines | Central Freight Lines | yes |
| r18 | City | Las Vegas | Las Vegas | yes |
| r18 | County | Clark | Clark | yes |
| r18 | Notification |  | (no such column in image; blank) | yes |
| r19 | Received Date | 12/16/2021 | 12/16/2021 | yes |
| r19 | Effective Date | 12/13/2021 | 12/13/2021 | yes |
| r19 | Type | Closure | Closure | yes |
| r19 | Affected Total | 50 | 50 | yes |
| r19 | Employer | Central Freight Lines | Central Freight Lines | yes |
| r19 | City | Reno | Reno | yes |
| r19 | County | Washoe | Washoe | yes |
| r19 | Notification |  | (no such column in image; blank) | yes |
| r20 | Received Date | 12/16/2021 | 12/16/2021 | yes |
| r20 | Effective Date | 12/17/2021 | 12/17/2021 | yes |
| r20 | Type | Layoff | Layoff | yes |
| r20 | Affected Total | 61 | 61 | yes |
| r20 | Employer | Monitronics International/Brinks Home | Monitronics International/Brinks Home | yes |
| r20 | City | Las Vegas | Las Vegas | yes |
| r20 | County | Clark | Clark | yes |
| r20 | Notification |  | (no such column in image; blank) | yes |
