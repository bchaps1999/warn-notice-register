# Source-backed recovery of lost history (started 2026-09-30)

v1.0 dropped the Big Local News mirror rows under the agency-only policy, and
v1.2.0 (80,703 notices) holds about 11,500 fewer notices than the 2026-09-14
export. This note records the work to restore that history from official
agency sources only, state by state.

## Decisions

- **2026-09-30. Hawaii paused.** `states.yaml` sets Hawaii to `archive` with no
  cadence. The upstream collector's current output re-keys every released
  Hawaii row, so the weekly sweep would have added about 390 duplicates. The
  published Hawaii notices stay in the register. Scheduled collection resumes
  once a key migration maps the new keys onto the released ones.

## Kentucky, Tennessee, Louisiana and Michigan archives (candidate, 2026-09-30)

**Status: isolated candidate, not released.** Nothing in `data/exports`,
`data/warn.sql.gz` or the site was written. Code: working tree on `fc556e3`
(uncommitted at replay time).

### Evidence (new dated paths, each with `manifest.json`)

| Path | Contents | Provenance |
|---|---|---|
| `data/source_snapshots/ky/kcc-2026-09-30/` | 1998–2016 tracking workbook, 2017–Feb 2025 report workbook, 2025 report CSV | downloaded live from kcc.ky.gov 2026-09-30 (~14:50 UTC) |
| `data/source_snapshots/tn/wayback-2026-09-30/` | "WARN Summary by Month" PDF (notice dates 2012-01-02..2017-10-26), 9 reports-page captures 2018-01..2021-12 | Wayback `id_` captures |
| `data/source_snapshots/la/wayback-2026-09-30/` | `WarnNotices{2007..2024}.pdf`, one capture per year | Wayback `id_` captures |
| `data/source_snapshots/mi/wayback-2026-09-30/` | old michigan.gov year pages 2014–2020, main pages 2020-07/2020-11/2022-01, 2021 year page | Wayback `id_` captures |

Each Wayback file's SHA-1 matches the CDX digest of its capture (CDX queried
2026-09-30); manifests record original URL, timestamp, capture UTC, bytes,
SHA-256 and data rows. The LA 2008 capture of 2008-11-24 has identical tables
to the 2011 one and is not used.

### Rules

- Projectors: `ky_source.project_archive`, `tn_source.project_archive`,
  `la_source.project_archive`, new `mi_archive_source.project`, hooked into
  `offline_rebuild` for `agency/{ky,tn,la,mi}_archive`. `source_bundle
  add-agency` adds a pinned directory to a bundle and never replaces one.
  These are replay-only archive tables, like the OH/MO/GA annual tables; the
  live scrape does not read them.
- Identity: KY Notice Number, else the Salesforce notice-document id, else the
  workbook row; TN WARN number (`2019008` and `20190008` are one number);
  MI michigan.gov document id; LA and the TN month report have none, so the
  table row. New dedupe keys only; overlaps with notices already in the build
  are held, never merged.
- Dates: KY `Date Received` stays `agency_received_date`; `Projected Date(s)`
  is the effective date (ranges keep both ends). TN page posting days are
  `agency_posted_date`. The TN report's `Notice Date` fills `notice_date` with
  no precision/basis, as the raw TN path does; it falls after `Received Date`
  in 313 of 511 rows, so its legal role is recorded as unverified. MI listing
  days are `agency_posted_date` only; MI notices have no canonical date. LA
  table notice dates fill `notice_date` with no basis.
- KY 2017 workbook counts typed into date-formatted cells use the stored cell
  number (e.g. 99, displayed as 1900-04-08). Ranges, "+/-", "TBD" and notes
  blank the count and keep the row.

### Row accounting

| State | Source rows | Admitted | Held by reason |
|---|---|---|---|
| KY | 1,227 (799 + 368 + 60) | 1,154 | amendment parent unresolved 33, KY 2025 CSV row with number 11, out of state 10, source column not WARN 12, already-admitted KY ID 4, duplicate row 2, rescinded 1 |
| TN | 1,178 (511 report rows + 667 distinct page texts) | 869 | earlier capture text of a WARN number 256, number reused for different employers 25 (7 numbers), already-admitted number 26, report row also on the page 2 |
| LA | 587 | 544 | update marker unresolved 25, continuation row 11, rescinded 7 |
| MI | 938 distinct listings | 583 | other capture's listing of the same document 342, update/revised listing 12, non-notice link 1 |

Notices (workers) added, by year (KY receipt year, TN posting/report notice
year, LA notice year, MI listing year):

| State | 1998–2004 | 2005–09 | 2010–14 | 2015–19 | 2020–24 | 2025 | unknown |
|---|---|---|---|---|---|---|---|
| KY | 242 (37,093) | 176 (15,195) | 218 (26,281) | 217 (25,971) | 246 (27,226) | 55 (5,286) | |
| TN | | | 343 (24,099) | 265 (35,913) | 261 (36,390) | | |
| LA | | 103 (14,284) | 156 (19,329) | 129 (13,695) | 156 (25,016) | | |
| MI | | | 50 (7,763) | 301 (42,626) | 230 (50,276) | | 2 (108) |

### Candidate build and replay

```bash
P=data/source_snapshots/2026-09-30-v1.2-source-bundle.tar.gz
# add-agency in order ky, tn, la, mi (each from the previous output):
python -m warnlive.migrate.source_bundle add-agency $P \
  --artifacts data/source_snapshots/ky/kcc-2026-09-30 --name ky_archive --out step-ky.tar.gz
# ... tn/wayback-2026-09-30 tn_archive, la/wayback-2026-09-30 la_archive,
#     mi/wayback-2026-09-30 mi_archive -> 2026-09-30-recovery-ky-tn-la-mi-source-bundle.tar.gz
python -m warnlive.migrate.offline_rebuild --bundle <bundle> \
  --quality-evidence-dir data/source_snapshots/2026-09-24-quality-evidence \
  --db <run>/candidate.sqlite --observed-at 2026-09-30 --source-only \
  --report <run>/report.json --exceptions <run>/candidate.exceptions.jsonl
```

- Bundle (session scratchpad, not committed): SHA-256
  `6d388cfa700aa27f8987a1103d199c965fbe19b3223442d66837769aac4bf70c`,
  2,141 files, 117,771,889 source bytes; rebuilding the chain reproduced it
  byte for byte.
- Result: 83,853 notices (+3,150), 98,240 versions, 8,611,740 workers
  (+406,551), 1,969 links (unchanged), ledger 6,482 rows (+780), integrity
  `ok`, 0 foreign-key errors.
- Fingerprints: notices `cd1222d6…6578`, versions `84c0da7f…c492`, links
  `d9344a4e…4195` (unchanged). Two independent replays matched, and their
  ledgers (SHA-256 `fb175a8d…5b27`) were byte-identical.
- Released keys: every one of the 80,703 v1.2 keys is present with an
  identical notice row and identical versions (compared against a replay of
  the v1.2 bundle with the same code, which itself reproduced the v1.2
  report's fingerprints and ledger checksum). No link changed.
- A scratch export of the candidate wrote 83,853 national rows; the site was
  not built.
- Tests: `pytest tests/ -q` 641 passed, 1 skipped.

### Unresolved and gaps

- Held rather than resolved: KY amendment rows (33), TN numbers the agency
  reused (7 numbers, about 16 events), LA "Update:" stacks (25) and MI
  update listings (12). Their original notices are admitted only where the
  source lists them separately.
- KY: no workbook ID before 2017; 759 notices are identified by workbook row.
- TN: no source for notices before 2012; page records never captured between
  the listed captures are missing.
- LA: the 2024 table is an August 2024 capture (partial year); the 2019 table
  was captured 2019-12-22.
- MI: no archived agency listing covers February 2022 through 2023; 2021 has
  only 12 listings from two captures. The listing day is a posting date.

## Connecticut, Iowa and Ohio archives; Oregon partial rows (candidate, 2026-09-30)

**Status: isolated candidate, not released.** Nothing in `data/exports`,
`data/warn.sql.gz` or the site was written. Code: working tree on `960932b`
(uncommitted at replay time).

### Evidence (new dated paths, each with `manifest.json`)

| Path | Contents | Provenance |
|---|---|---|
| `data/source_snapshots/ct/wayback-2026-09-30/` | CT DOL "Listing of WARN Notices" pages `warnYYYY.htm`, 2010–2012 and 2014–2025 | Wayback `id_` captures; no capture exists for 2005–2009 or 2013 |
| `data/source_snapshots/ia/wayback-2026-09-30/` | IWD `WARN_20200420-2.xlsx` (2015-04..2020-04), `WARN_20180503.xlsx` (2011-01..2018-05), `warn_20150812.pdf` (2005-07..2015-08) | Wayback captures of iowaworkforcedevelopment.gov; the 2018 workbook is byte-identical to the copy Big Local News hosts but is cited from IWD |
| `data/source_snapshots/oh/wayback-2026-09-30/` | ODJFS 2023 and 2024 annual pages (captured 2025-06-06), current-year page captured 2024-12-28 and 2025-10-31 | Wayback `id_` captures |

Every file's SHA-1 matched the CDX digest of its capture (CDX queried
2026-09-30). Partial years: the CT 2012 page was captured 2012-12-20 and the
2025 page 2025-07-04; the OH 2025 page lists notices received through late
October 2025. The CT monthly 1998–2004 pages and per-notice PDFs were not
fetched.

### Rules

- Projectors: new `ct_archive_source.project`, `ia_source.project_archive`,
  new `oh_archive_source.project`, hooked into `offline_rebuild` for
  `agency/{ct,ia,oh}_archive`. Replay-only, like the earlier archive tables.
- CT: no notice ID, so the unit is the table row (`CT:archive:<year>:r<row>`).
  The WARN Date fills `notice_date` with no precision or basis; the "Rec'd"
  date is `agency_received_date` ("No Date"/"Not Dated" leaves `notice_date`
  null). Layoff dates are a whole-cell date or an ordered range; counts are
  whole-cell integers ("13 total: 2 CT residents" blanks the count). Rows
  marked as updates or revisions (446, mostly Stanadyne's repeated
  small-batch updates of 2010–2012) are held: the pages do not say what an
  update changes. 2010 rowspan groups (Shaw's 8 sites, Electric Boat phases)
  share `filing_group` and are not summed. Nine rows narrower than the
  header (e.g. six Dollar Express rows, 2017) are admitted with employer and
  dates only; their other cells cannot be assigned to columns.
- IA: the archived rows go through the event-log rules (`project`, including
  `_amendment_version`) after two removals: an event printed in more than
  one archived log is cited from the newest log (an exact repeat is a
  duplicate capture, a changed listing is held; the PDF cuts names at 30
  characters, so names match on a prefix of at least 12 characters), and an
  event also in the current logs (`agency/ia`, from 2018-06-18) is held, as
  is an amendment whose parent may be a current-log notice. "Ammendment" now
  counts as an amendment type (no current-log type is spelled so), and the
  archive maps "Closure"/"Layoff" types.
- OH: one notice per Notice ID with `classify_agency_ids` and the
  `oh_annual_source` key (`OH|source|<id>`). IDs already read by the build
  (admitted or held, including the 2022 table and the live page), irregular
  IDs (`012-023-029`, `009-2-004`, `007-24/052`), phase/conflict groups and
  update-only listings are held. Contact and link cells do not count as a
  content change between captures.
- OR (`--or-historical-partial-rows`, off by default): a singleton historical
  WARN number missing only its layoff date or worker count is admitted with
  that field null, per the rule that a missing optional fact does not
  exclude an identifiable notice. Still held: 10 rows whose Company Name is
  only a place or PO box (same defect as the six already pinned), WARN#
  `0000`, and 0942/1004 (Amalgamated Sugar, two numbers on one received day,
  possibly one filing entered twice). All 45 admitted rows lack a layoff
  date; 32 also lack a count.

### Row accounting

| State | Source rows | Admitted | Held by reason |
|---|---|---|---|
| CT | 939 | 490 | update/revision 446, rescinded 1, continuation 1, duplicate row 1 |
| IA | 1,054 (352 + 339 + 363) | 382 notices + 63 amendment versions | duplicate capture 349, listed in current logs 131, amendment without parent 69, amendment parent ambiguous 38, possible revision 6, differs in newer log 5, layout/date 5, site/worker allocation 4, repeated event 2 |
| OH | 351 (246 Notice IDs) | 236 | duplicate capture 90, conflicting ID 13, update-only listing 4, invalid ID 4, superseding amendment 3, already in build 1 |
| OR historical | 1,082 | 828 (+45) | as before except: incomplete 58 → 0; place-only employer 6 → 16, placeholder number 1, same employer and received day 2 |

Notices (workers) added, by year (CT WARN date, IA notice date, OH and OR
received date):

| State | 1993–2004 | 2005–09 | 2010–14 | 2015–19 | 2020–24 | 2025 | unknown |
|---|---|---|---|---|---|---|---|
| CT | | 1 (155) | 170 (12,406) | 138 (11,155) | 169 (21,033) | 12 (567) | |
| IA | | 137 (13,462) | 87 (10,605) | 158 (15,867) | | | |
| OH | | | | | 165 (19,727) | 68 (7,281) | 3 (862) |
| OR | 33 (3,254) | 9 (0) | 2 (0) | | 1 (0) | | |

The six CT notices with no WARN date are banded by their received date.

### Candidate build and replay

```bash
B=recovery-candidate/2026-09-30-recovery-ky-tn-la-mi-source-bundle.tar.gz  # sha 6d388cfa…f70c
python -m warnlive.migrate.source_bundle add-agency $B \
  --artifacts data/source_snapshots/ct/wayback-2026-09-30 --name ct_archive --out step-ct.tar.gz
# then ia/wayback-2026-09-30 ia_archive, oh/wayback-2026-09-30 oh_archive
#   -> 2026-09-30-recovery-ct-ia-oh-source-bundle.tar.gz
python -m warnlive.migrate.offline_rebuild --bundle <bundle> \
  --quality-evidence-dir data/source_snapshots/2026-09-24-quality-evidence \
  --db <run>/candidate.sqlite --observed-at 2026-09-30 --source-only \
  --report <run>/report.json --exceptions <run>/candidate.exceptions.jsonl \
  --or-historical-partial-rows
```

- Bundle (session scratchpad, not committed): SHA-256
  `3b26d7fd33c920560882127054645918438ef11f43f1785e024ae1382f633b21`,
  2,166 files, 122,172,493 source bytes.
- Result: 85,006 notices (+1,153 over the KY/TN/LA/MI candidate), 99,456
  versions, 1,992 links (+23 `sibling_entry` from CT and IA filing groups),
  8,728,114 workers, ledger 7,610 rows, integrity `ok`, 0 foreign-key errors.
- Fingerprints: notices `1ab5b241…3508`, versions `fe198bda…bfd4`, links
  `cc06649a…5f27`. Two independent replays matched, and their ledgers
  (SHA-256 `b3f07794…fc4e`) were byte-identical.
- Released keys: all 80,703 v1.2.0 keys present with identical notice rows and
  versions, no link lost (compared with a replay of the v1.2 bundle under
  this code).
- The v1.2 bundle under this code without the option reproduced the v1.2
  report's counts, fingerprints and ledger byte for byte (the CI check). With
  `--or-historical-partial-rows` it gives 80,748 notices (+45, all Oregon),
  95,135 versions, 8,208,443 workers, links unchanged; notices `b955c6c7…9fb`,
  versions `0d979751…3b56`; the ledger differs only in 71 Oregon rows (58
  removed, 13 added). The release-replay baseline must be re-recorded when a
  release adopts the option.
- Tests: `pytest tests/ -q` 656 passed, 1 skipped.

### Unresolved and gaps

- CT: 446 update rows held without linking to their originals; no pages for
  2005–2009 or 2013; 2012 and 2025 partial; monthly 1998–2004 pages unused.
- IA: amendment rows without a unique parent (107) stay held; 2019–2020
  events come from the current logs only.
- OH: 13 rows under IDs with conflicting content or phases (David's Bridal,
  Crothall, Big Lots, INOAC) are held; captures after 2025-10-31 were not
  used, so late-2025 notices not on the live page may be missing.
- OR: the current capture's 3 `incomplete_agency_row` holds are unchanged
  (that path mirrors the live scrape).

## Arizona, Maine, Vermont and Delaware job portals (candidate, 2026-09-30)

**Status: isolated candidate, not released.** Nothing in `data/exports`,
`data/warn.sql.gz` or the site was written. Code: working tree on `9f3d700`
(uncommitted at replay time).

These four states use the same job-portal platform as Kansas. Upstream
warn-scraper's year search now gets HTTP 400; only the WARN-filtered search
(`/search/warn_lookups?commit=Search&q[notice_eq]=true&page=N`) works. v1.2
replays their July upstream captures (AZ 208 rows/189 notices, ME 9, VT 51/49,
DE 46, 14 of DE's typed "Non-WARN").

### Code

- `migrate/job_portal_source.py` generalizes the Kansas capture, staging and
  freeze code over a `Portal` (base URL, labels); `migrate/ks_portal_source.py`
  keeps the Kansas bindings. Re-staging the 2026-09-30 Kansas evidence with it
  reproduces `ks.csv` (SHA-256 `35dd5b2f…fe23`), `holds.jsonl` and
  `field_warnings.jsonl` byte for byte.
- `fetch/custom/job_portal.py` is the live collector for KS, AZ, ME, VT and DE
  (`fetch/custom/<postal>.py`); `states.yaml` switches AZ/ME/VT/DE to
  `source: custom`. `min_rows` stays below each July capture that the v1.2
  replay verifies (AZ 200, ME 5, VT 40, DE 40); fetch budgets AZ 75 minutes,
  others 20.
- `job_portal_source.project` replays a frozen capture pinned as
  `agency/<postal>_portal`: it re-stages the HTML (the archived staged CSV and
  holds must reproduce), normalizes the staged CSV exactly as the live scrape
  does, and holds any record whose portal number is already in the bundle's
  `raw/<postal>.csv` (`record_in_earlier_capture`, keeping any staging reason
  as `staging_hold_reason`) or whose key is already admitted.
- The live scrape no longer freezes absence for these states (as for KS):
  their CSV omits held and non-WARN listings.

### Identity and holds

The four states keep the legacy key (state, employer, notice date, location).
Every v1.2 row maps to a portal record number through `record_number` and
`detail_page_url`, every released record is still listed except DE's 14
Non-WARN rows (the collector reads WARN listings only), and no released
record's key changes when read from the new capture. The released notices
therefore stay on the July raw rows unchanged. Applying the Kansas event hold
to the full capture instead would have removed 35 released AZ and 4 released
VT notices (v1.2 published same-employer/day groups such as Hostess 2012,
Life Care Centers 2015 and Intel 2016), and replacing `raw/de.csv` would have
dropped the 14 Non-WARN notices.

New records follow the Kansas staging holds: same employer and notice day,
same-day name variants, missing notice date, listing/detail disagreement.

### Evidence

`data/source_snapshots/{az,me,vt,de}/portal-2026-09-30/`: `manifest.json`
(capture times, request counts, no failures) and a `freeze_capture` archive of
every listing and detail page with inventory, staged CSV, holds and
provenance. The pages were fetched 2026-09-30 14:58–15:27 UTC at a 2 s pace;
`inventory.json` was rebuilt from the saved pages without refetching.

| State | Archive SHA-256 | Listed | Staged | Held at staging |
|---|---|---:|---:|---:|
| AZ | `58298c57…4406` | 767 | 685 | 82 |
| ME | `f1e9dfb9…b0a9` | 94 | 77 | 17 |
| VT | `c4dbfed9…5bc9` | 101 | 93 | 8 |
| DE | `3255dc69…b92b` | 80 | 76 | 4 |

### Row accounting

| State | Listed | Admitted | Held by reason |
|---|---:|---:|---|
| AZ | 767 | 531 | in July capture 208 (54 of them also staging holds), same employer/day 26, name variant 2 |
| ME | 94 | 68 | in July capture 9, same employer/day 17 |
| VT | 101 | 48 | in July capture 51 (6 also staging holds), same employer/day 2 |
| DE | 80 | 44 | in July capture 32, same employer/day 4 |

Notices (workers) added, by notice year:

| State | 1998–2004 | 2005–09 | 2010–14 | 2015–19 | 2020–24 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| AZ | | | 76 (12,971) | 110 (13,972) | 303 (47,296) | 31 (7,686) | 11 (779) |
| ME | | | 2 (693) | 15 (2,010) | 42 (3,916) | 7 (632) | 2 (156) |
| VT | 6 (557) | 1 (189) | 5 (558) | 16 (1,446) | 14 (1,380) | 6 (361) | |
| DE | | 19 (3,929) | 8 (2,567) | 8 (859) | 6 (965) | 2 (153) | 1 (106) |

### Candidate build and replay

```bash
B=recovery-candidate-2/2026-09-30-recovery-ct-ia-oh-source-bundle.tar.gz  # sha 3b26d7fd…3b21
python -m warnlive.migrate.source_bundle add-agency $B \
  --artifacts data/source_snapshots/az/portal-2026-09-30 --name az_portal --out step-az.tar.gz
# then de, me, vt (<st>_portal) -> 2026-09-30-recovery-job-portals-source-bundle.tar.gz
python -m warnlive.migrate.offline_rebuild --bundle <bundle> \
  --quality-evidence-dir data/source_snapshots/2026-09-24-quality-evidence \
  --db <run>/candidate.sqlite --observed-at 2026-09-30 --source-only \
  --report <run>/report.json --exceptions <run>/candidate.exceptions.jsonl \
  --or-historical-partial-rows
```

- Bundle (session scratchpad, not committed): SHA-256
  `968ea8790aa49fd330b2f3173714e55bebf03a3831e65e0da3a094e62e983c84`,
  2,174 files, 122,818,457 source bytes.
- Result: 85,697 notices (+691), 100,147 versions, 1,992 links (unchanged),
  8,831,295 workers (+103,181), ledger 7,961 rows (+351), integrity `ok`,
  0 foreign-key errors.
- Fingerprints: notices `99ec1a1c…1e1a`, versions `08c90f29…421f`, links
  `cc06649a…5f27`. Two independent replays matched, and their ledgers
  (SHA-256 `1aa650b7…7453`) were byte-identical.
- Released keys: all 80,703 v1.2.0 keys present with identical notice rows
  and versions, no link lost (compared with a replay of the v1.2 bundle under
  this code, which reproduced the v1.2 report's counts, fingerprints and
  ledger byte for byte).
- Live parity: `warnlive scrape me vt de az` with the new collectors against
  a copy of the candidate database (isolated workdir and data dir; real
  portal fetch 2026-09-30 13:15–13:53 UTC at a 2 s pace) returned `ok` for all
  four with 0 new notices (updated AZ 98, ME 3, VT 22, DE 18). Its staged
  CSVs were byte-identical to the frozen captures. The updates are released
  notices whose July row had no worker count and whose detail page now has
  one; the rebuild keeps the July rows, so the first live run after release
  adds these versions.
- Tests: `pytest tests/ -q` 667 passed, 1 skipped.

### Unresolved and gaps

- **Do not merge the `states.yaml` switch ahead of a release that adopts this
  candidate.** On the current v1.2 database the same live run adds 691
  notices and fails the scrape's regression gate (AZ and ME worker totals
  grow more than 5x, AZ `no_jobs` changes), so the weekly scrape would stop
  publishing until the baseline is re-recorded. The committed health
  snapshot already fails against the local v1.2 database (total 80,822 →
  80,703, MA 568 → 550) independently of this change.
- Held: 28 AZ, 17 ME, 2 VT and 4 DE new records in same-employer/day groups
  or name variants. The released v1.2 groups (Hostess, Life Care Centers,
  Intel, and others) stay as published, folded by legacy key.
- A later bundle that overlays `raw/<state>.csv` with the collector's staged
  CSV would drop the released rows the staging holds (54 AZ, 6 VT) and DE's
  14 Non-WARN rows; keep the July captures in the bundle.
- The collector refetches every page weekly (AZ about 28 minutes); a
  published notice later joined by a same-employer/day record stays
  published while the new record is held (the Kansas durable-hold gate is
  not applied: it would block on released v1.2 groups).
- DE record 40 (Hostess 2012) is reachable but unlisted and untyped; it is
  out of scope.

## Independent review and final candidate (2026-09-30)

**Status: isolated candidate, not released.** Code: working tree on `1899c79`
(uncommitted at replay time). This section corrects the three candidate
sections above; their tables stay as recorded.

### What the review checked

An independent review of the combined KY/TN/LA/MI, CT/IA/OH/OR and job-portal
candidate re-derived row accounting per source, compared every released v1.2
key with the candidate, searched the new notices for repeated events
(same place, dates and count under different names), and traced a sample of
admitted rows to their source cells. It found:

1. **IA archive repeats under retyped names.** The 12-character name prefix
   missed agency typos, so five events were admitted twice: `eiber` (Iowa
   City, 2018-06-26, 23 workers, `WARN_20200420-2…xlsx:r222`) is the current
   log's ACT, Inc. at the same address (2727 S. Scott Blvd, a v1.2 notice);
   "Clipper Windposer" (Cedar Rapids 2012-10-16, 10), "IPSCO Tubulars Inc."
   (Camanche 2015-05-06, 80), "Verizon Corporate Resporces G" (2015-07-16,
   102) and "SSP America, Ic." (2017-04-28, 57) repeat rows of a newer
   archived log.
2. **KY tracking-sheet re-entries.** `duplicate_row_in_source` caught only
   identical rows. BSC Acquisition (2001 r9/r12, 59), Atlantis Plastics
   (2008 r28/r29, 152), Panasonic (2008 r34/r37, 51; projected
   `09/30/2008 - 03/31/2009` against a 2008-09-30 date cell) and Appalachian
   Fuels (2009 r59/r60) were each admitted twice.
3. **Live/rebuild parity for the new portals.** The live pipeline read
   `<postal>.hold_policy.json` for Kansas only.

### Fixes

- IA (`ia_source._same_event`): an archived row whose city, notice date,
  layoff date and worker count all equal a newer archived log's row or a
  current-log row is matched regardless of name. A newer-log match is a
  `duplicate_agency_capture` (or `listing_differs_in_newer_archived_log` if
  the amendment type differs); a current-log match is
  `listed_in_current_ia_logs`, the reason the name match already used. The
  held row's cells stay in the ledger with `related_source_row` pointing at
  the kept row. The rule also matched 8 PDF amendment rows to their 2018
  workbook copies ("Lennox" vs "Lennox Industries, Inc.", "Electrolux" vs
  "Electrolux Home Products, Inc."); two of them (Lennox 2012-03-29 and
  2013-02-28) had been admitted as versions 2 and 3 of the 2008 Lennox
  notice from the older PDF while the newer workbook's copy was held without
  a verified parent. They now follow the newest-log rule and are held.
- KY (`ky_source._reentry_key`): a tracking-sheet row with the same
  normalized employer, county, address, Date Received, projected date
  (parsed start, else the cell text) and employee count as an earlier row is
  held as `duplicate_row_in_source` with `differing_columns`, unless the two
  rows name different affected occupations. Two "See ..." pointers to the
  notice count as the same. So Kuhlman Electric (2009 r33/r35, "Senior
  Engineer" / "Senior Administrative Assistant") and ArvinMeritor (2009
  r54/r55, "See W.A.R.N." / "Production supervisor"), one worker each, stay
  separate notices: a named occupation is a substantive difference, and the
  source gives no evidence that either row repeats the other.
- Portals (`pipeline._run_one`): for AZ, ME, VT and DE the live run now
  requires the collector's current hold policy (on a live custom fetch),
  checks its `raw_sha256` against the CSV, excludes any CSV row whose record
  ID or employer/day matches a staging hold
  (`reviewed_portal_event_identity_unresolved`), and lists the staging-held
  record IDs under `admission.staging_held_ids` (Kansas too). Staging holds
  are not CSV rows, so they are not counted in `excluded_rows`. The Kansas
  reviewed/durable hold lists and publication block are unchanged and remain
  Kansas-only. The rebuild's `record_in_earlier_capture` and
  `same_key_as_admitted_notice` holds have no live counterpart (a live run
  reads one capture and stores a same-key row as a version); the latter held
  0 rows in this replay.

### Notes the review asked to record

- **Oregon historical rows cite a secondary-hosted agency file.** The 783
  Oregon historical notices in v1.2 and the 45 partial rows admitted with
  `--or-historical-partial-rows` come from the Oregon agency workbook hosted
  on Big Local News's GitHub: an exact copy of an agency-supplied file (see
  `docs/agency-tx-or-historical-expansion-2026-09-24.md`), not a file
  fetched from an Oregon site.
- **KY 2024 report rows 9 and 10 (PARSONS CORPORATION, Madison).** Both were
  received 2024-11-04 with a 2025-01-16 projected date; row 9 (4 workers)
  has no notice URL and is keyed by workbook row, row 10 (7 workers) by its
  notice document. The counts differ, so both stay admitted; they may be one
  filing.
- **Unverified notice-date roles.** The TN month report's `Notice Date` and
  the CT pages' `WARN Date` fill `notice_date` with no basis. In the TN
  report, 313 of the 509 admitted rows have a Notice Date after the Received
  Date (CT: 6 of 490 after the Rec'd date). Exclude these rows from strict
  notice-timing cohorts until the roles are verified.
- **Released IA repeats (not changed).** The same search over all IA notices
  still finds 21 pairs, all between v1.2 released notices: 18 are typo or
  truncation repeats between the current `event-log.xlsx` and `historical-2023.pdf`
  (e.g. "Lennox Industries"/"Lennox Industires", 2022-12-20, 114), and 3 are
  plausibly distinct (two MetaBank branch pairs, Conde Group / Integrated
  Human Capital at 1 worker). Correcting them changes released keys and needs
  its own reviewed migration.

### Final candidate build and replay

The fixes are projection-only; the bundle is the job-portal bundle above
(`968ea8790aa49fd330b2f3173714e55bebf03a3831e65e0da3a094e62e983c84`, reused,
not rebuilt).

```bash
python -m warnlive.migrate.offline_rebuild \
  --bundle recovery-candidate-3/2026-09-30-recovery-job-portals-source-bundle.tar.gz \
  --quality-evidence-dir data/source_snapshots/2026-09-24-quality-evidence \
  --db <run>/candidate.sqlite --observed-at 2026-09-30 --source-only \
  --report <run>/report.json --exceptions <run>/candidate.exceptions.jsonl \
  --or-historical-partial-rows
```

- Held by the fixes (IDs in the previous candidate): IA 66192, 66269, 66270,
  66271, 66431; KY 67769, 67940, 67946, 68033; versions 2 and 3 of IA 66371
  (Lennox).
- Result: 85,688 notices (−9), 100,136 versions (−11), 1,992 links
  (unchanged), 8,830,761 workers (−534), ledger 7,972 rows (+11), integrity
  `ok`, 0 foreign-key errors.
- Row accounting: IA archive 1,054 rows = 377 notices + 61 versions + 616
  held (duplicate capture 361, listed in current logs 132, amendment without
  parent 64, amendment parent ambiguous 37, possible revision 6, differs in
  newer log 5, layout/date 5, site/worker allocation 4, repeated event 2).
  KY archive 1,227 rows = 1,150 admitted + 77 held (duplicate row 6, others
  as before).
- Fingerprints: notices `0059cf2f…388d`, versions `1f274df6…8c668`, links
  `cc06649a…5f27`. Two independent replays matched, and their ledgers
  (SHA-256 `e9838342fef528e7cd9eb6204cc1bebf2897672461e04f3c5994c0b2e7009805`)
  were byte-identical.
- Released keys: compared with the v1.2.0 database (`git show
  5ce24af:data/warn.sql.gz`), all 80,703 keys are present with identical
  notice rows (every column but `id`) and versions, and no link was lost.
- The v1.2 bundle under this code without the option reproduced the v1.2
  report's counts, fingerprints and ledger (5,702 rows, `16c5e516…5c7b`).
- IA check: no pair of IA notices with the same location, notice date,
  effective date and count under different names involves an archive row
  (the 21 remaining pairs are the released ones above).
- Tests: `pytest tests/ -q` 672 passed, 1 skipped.
