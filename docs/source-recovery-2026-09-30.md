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
