# Original-source recovery review, 2026-09-24

The subsequent staged recovery is documented in the
[original-source expansion](agency-original-source-expansion-2026-09-24.md).
The later [Ohio expansion](oh-original-source-expansion-2026-09-24.md)
recovers 854 agency records from 2015–22; the comparison gap and Ohio lead
below describe the earlier review point, before that recovery.

This is an acquisition review following the [agency-only rebuild](agency-only-rebuild-2026-09-23.md)
and [first coverage expansion](agency-coverage-expansion-2026-09-23.md). At
this review point, none of the newly pinned evidence had been admitted to the
candidate.
The remaining 25,036-record difference from the earlier BLN-containing
candidate is a comparison gap, not a verified count of missing legal filings.

| State | Comparison gap | Original-source lead | Immediate constraint |
| --- | ---: | --- | --- |
| TX | 5,075 | [TWC WARN page](https://www.twc.texas.gov/data-reports/warn-notice) pins 2020–26 workbooks. The [official Texas open-data dataset](https://data.texas.gov/dataset/Worker-Adjustment-and-Retraining-Notification-WARN/8w53-c4f6/about_data) is also online. A public [scraper provenance note](https://warn-scraper.readthedocs.io/en/latest/scrapers/tx.html) links a mirror of an allegedly TWC-supplied 1989–2019 workbook, and older TWC annual workbook URLs appear in contemporary references. | Check direct agency/archived copies and the mirrored workbook's provenance, then reconcile overlap and rows that TWC may not count as WARN. A request to TWC remains useful for authentication and field definitions. |
| NY | 4,494 | [NYS WARN dashboard](https://dol.ny.gov/warn-dashboard) embeds a public Tableau workbook with annual CSV exports. Local cache holds 9,034 rows across 2006–26, only 758 of which (2022–24) were previously pinned. | Re-fetch and pin each year, then distinguish filings, sites, and amendments. The [2020 state PDF](https://dol.ny.gov/2020-warn-notices) has 2,390 posted entry lines, including amendments, so list rows are not automatically unique notices. |
| GA | 4,407 | [TCSG Rapid Response](https://www.tcsg.edu/worksource/rapid-response/) links five archived 2018–22 WARN filing reports. They contain 789 distinct published IDs; 787 occur in the excluded historical cache. | Verify PDF row fields and old-cache correspondence; 2017 and earlier still lack an identified original archive here. |
| KS | 2,000 | [KANSASWORKS WARN search](https://www.kansasworks.com/search/warn_lookups?commit=Search&q%5Bnotice_eq%5D=true) currently shows 910 WARN records. The excluded 2,126-row capture contains 1,251 records marked Non-WARN. | Capture public WARN listing and detail pages at a rate the portal allows, then reconcile record numbers and repeated events. |
| OH | 1,083 | ODJFS publishes individual WARN PDFs in annual asset folders, including [2023](https://dam.assets.ohio.gov/image/upload/jfs.ohio.gov/warn/WARN%202023/GXO.pdf) and [2025](https://dam.assets.ohio.gov/image/upload/jfs.ohio.gov/warn/WARN%202025/OhioRecoveryCenter.pdf). Earlier candidate counts assign the 1,083 difference to 2015–25. | Enumerate those years and establish document identity; the retained 2001–14 agency PDFs and 56 current 2026 rows do not cover them. |
| OR | 1,034 | [HECC WARN list](https://ccwd.hecc.oregon.gov/Layoff/warn) has notice attachments and 194 track rows in ten pages at this review. | The page says HECC retains WARN records for six years; the oldest listed row is September 2020. Historic filings require another preserved agency archive. Multi-site/phase rows need the attachments to establish identity. |
| TN | 855 | [Tennessee WARN archive](https://www.tn.gov/workforce/general-resources/major-publications0/major-publications-redirect/reports.html.html) exposes 2021–24 rows already used in the candidate. | The public archive located here does not expose earlier years; seek original older state records rather than reuse the removed BLN append. |

## Additional agency-supplied historical file leads

The Stanford Big Local News [source notes](https://warn-scraper.readthedocs.io/en/latest/sources.html)
identify two other historical files with documented agency correspondence:

- [New York](https://warn-scraper.readthedocs.io/en/latest/scrapers/ny.html):
  NY DOL prepared a 2016–21 Excel file for the project; BLN hosts a
  [copy](https://storage.googleapis.com/bln-data-public/warn-layoffs/ny_historical.xlsx).
  This may help resolve rows held from the direct annual dashboard exports,
  but overlaps those exports and is not a separate count of filings.
- [Oregon](https://warn-scraper.readthedocs.io/en/latest/scrapers/or.html):
  an agency contact sent a [July 2021 WARN list workbook](https://github.com/biglocalnews/WARN/files/6819514/OR.WARNList.July.2021.xlsx)
  reportedly reaching back to 1980. It is a promising direct-origin lead for
  the Oregon historical gap; its rows and interpretation need verification.

These are secondary-hosted copies of agency-origin files, like Texas's
historical workbook. Their provenance and overlap should be recorded before
admission.

## Newly pinned evidence

The TCSG-linked [Georgia archive folder](https://www.dropbox.com/sh/ebrunrkg0mym3yz/AACfRhkNrCHIlimR30_jGS5pa?dl=0)
contains five PDFs for 2018, 2019, 2020, 2021, and 2022. They are preserved at
[`data/source_snapshots/ga/archives-2018-2022`](../data/source_snapshots/ga/archives-2018-2022/manifest.json)
with URLs, sizes, SHA-256 checksums, and observed ID counts: 37, 77, 551, 64,
and 60. The 789 IDs are unique across those PDFs; 787 match an ID in the
excluded `workdir/cache/ga/ga_historical.csv`. This verifies a tractable
original-source subset, not a count of newly admissible distinct events.

The current 2020 New York dashboard CSV is pinned with a separate
[research manifest](../data/source_snapshots/ny/research-2020-manifest.json).
It has 2,169 rows, matching the older local 2020 cache count. At the same row
ordinals, notice date, layoff start, posting date, and worker count are
unchanged. Twenty rows differ in text, mostly character encoding in employer
names; one address string also changed. The new file has not been added to the
rebuild bundle. The older 2019 state PDF is discoverable in search but its
direct URL returned HTTP 403 during this review.

## Practical order

1. Pin all available New York dashboard years directly and build an explicit
   filing/site/amendment identity review. The cache alone is insufficient
   evidence for admission, but the verified public Tableau export route makes
   this the largest accessible source opportunity.
2. Re-capture Kansas from KANSASWORKS with record numbers and detail pages.
   Its long public history makes the apparent 2,000-record gap testable.
3. Project the five pinned Georgia PDFs conservatively by published GA ID,
   with direct row correspondence to the removed cache and no cache-sourced
   fields. Search separately for pre-2018 records.
4. Enumerate Ohio's 2015–25 official filing documents. Ask TWC for Texas's
   original pre-2020 files and field definitions. Review Oregon attachments;
   seek older Oregon and Tennessee agency archives separately.

Each recovery needs a new isolated replay and source-row exception accounting
before any claim of restored coverage. The checked-in public database and site
are still older artifacts.
