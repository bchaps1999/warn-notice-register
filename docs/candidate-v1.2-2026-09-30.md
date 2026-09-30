# v1.2 candidate: fresh source bundle and replay (2026-09-30)

**Status: promoted as v1.2.0 from the final build below (code `abac0db`).**
The sections after "Final build" describe the preceding build (`dce61c0`),
whose independent review found one more blocker.
This note records a new dated source bundle, its isolated replay, and how
every released v1.1.1 notice key is accounted for in the replay. Nothing in
`data/exports`, `data/warn.sql.gz`, `data/health/snapshot.json` or the site
was written.

This is the second build under this date. The first (code `0fc58fd`, bundle
SHA-256 `160c9dec…a5fd`) is recorded under "Superseded build" below. Its
review found blockers, which commits `aa7a0f4` and `500f6c3` fix. The bundle
was rebuilt under the same file name; the earlier one was never committed.

## Final build (code `abac0db`), promoted as v1.2.0

A second independent review of the `dce61c0` build confirmed the key
accounting, NY, NE, bundle provenance and exports against the underlying rows.
It found one blocker. CA's capture lists the current workbook first and then
the fiscal-year reports from newest to oldest, so a notice re-listed in two
reports took the older report's row as its current version. 97 notices were
affected; Broadcom Irvine, for example, showed 689 instead of the newer
report's 771.

Commit `abac0db` fixes this. `same_key_document_order: newest_first` puts a
key's cross-document listings in chronological order in `prepare_batch`, on
the live and replay paths alike. The same commit also changes the IA rule: a
"Change in date" amendment that reports a smaller count than the notice is a
phase, and no longer moves the whole notice's date (Tyson Perry keeps
2024-06-28).

The bundle is unchanged (SHA-256 `0414c3f5…026b`); only the replay was re-run.

- **Replay report:** `2026-09-30-v1.2-candidate-report.json`, SHA-256
  `5333093fda62979596f9baaa6278ef0427dca0244d91c687440de031811ab8be`.
- **Ledger:** `2026-09-30-v1.2-candidate.exceptions.jsonl.gz` (`gzip -n -9`),
  SHA-256 `89d1ce00e74eb30e4d0c8d81dc3f8b92b491f262c4754efacc60b3487bf921c9`.
  Uncompressed it has 5,702 rows, SHA-256 `16c5e516…c695c7b`.
- **Counts:** 80,703 notices, 95,090 versions, 8,205,189 workers,
  1,969 links, 5,702 ledger rows. Integrity `ok`, 0 foreign-key errors.
- **Fingerprints:** notices `ad39053c…`, versions `5aed02aa…`, links
  `d9344a4e…`. A second replay was identical, and its ledger byte-identical.
- **Key accounting:** 0 unexplained, 6,808 new keys (as in the `dce61c0` build).
- **Against `dce61c0`:**
  - Notices are unchanged.
  - 93 CA notices changed their current version. 0 CA notices now show an
    older report as current, down from 97.
  - CA workers: 2,328,500 → 2,331,254. No other state's workers changed.
  - One CA notice (David's Bridal, 2023-09-02) gained a version. Its FY23-24
    report lists it twice, differing only in apostrophe style, and both rows
    are now kept.
- **Exports:** the national CSV (80,703 rows, 8,205,189 workers) equals the
  sum of the state CSVs and the database.
- **Site:** `npm test` passed (3) and `npm run build` built.
- **Regression check** against the v1.1.1 snapshot: only the expected NE
  count and IA/MS location-fill failures. The snapshot is refreshed in the
  release commit.

## What ran

- **Code:** commit `dce61c0` (branch `data-quality-fixes`: `500f6c3` plus a
  merge of main's workflow-only change), in a clean detached worktree. Python
  was the checkout's `.venv` (3.13) with `PYTHONPATH` set to the worktree.
- **Base bundle** (frozen v1.1.1 input, unchanged):
  `data/source_snapshots/2026-09-24-strict-ks-ky-source-bundle.tar.gz`,
  SHA-256 `746745a066cb5e3c6ad5fc4263b83b27b3bba4a0d25115d46d78341e1df42070`.
- **New bundle:** `data/source_snapshots/2026-09-30-v1.2-source-bundle.tar.gz`,
  SHA-256 `0414c3f55de71bd34993b260360eb5c4d8b8ab4dd626a9f72d623ec7e4f3026b`
  (40,099,954 bytes, 2,095 files, 111,270,774 source bytes,
  `admission_inputs = agency-only-v1`). `source_bundle verify` passed.
- **Kansas companion evidence:** `data/source_snapshots/2026-09-30-ks-portal-evidence.tar.gz`,
  SHA-256 `424c32898ceaabcc1b5f91ac834ef0f8f12b3b3729b85c3c0e1506b9c9c1c0b6`
  (same bytes as the first build).
- **Replay report:** `data/source_snapshots/2026-09-30-v1.2-candidate-report.json`,
  SHA-256 `c1326b53da9d0e3dea73a2458bd9ff70ec33f06fbd93d2b8a0755a13f33bfdc3`.
- **Exception ledger:** `data/source_snapshots/2026-09-30-v1.2-candidate.exceptions.jsonl.gz`
  (`gzip -n -9`), SHA-256 `8cf71a0ad2c88f17bb7bedf0fd40d23685d352a417cbe0fdc0b088a850633206`.
  Uncompressed it has 5,702 rows, SHA-256
  `af53640f9583efd46f9f02c131f04f6779d4858d5404b46e8ae6e21d1d6c1d92`.

### 1. Fresh captures

```bash
# NE year reports were seeded from the pinned 2026-09-30 evidence, so the
# collector reads the same bytes (the pages print "Events as of <today>"):
cp data/source_snapshots/2026-09-30-ne-ndol-reports/archives/ne/{warn_report,layoff_closure_report}-* <fresh>/cache/archives/ne/
warnlive scrape --cadence weekly --smoke --workdir <fresh> --run-report <fresh>/run-report.json   # 02:23-03:13 UTC
warnlive scrape dc --smoke --workdir <fresh-dc> --run-report <fresh-dc>/run-report.json          # 03:46 UTC, patched collector
```

- **Weekly scrape:** 39 of 41 active states fetched.
  - DC failed with upstream `dc.py` (`MissingSchema: Invalid URL '/page/rapid-response'`).
  - VA failed locally (`Could not find Xvfb`).
  - An earlier attempt with `-v` was stopped after three states because pdfminer debug logging grew the log by 7 MB/s. Its output was discarded.
- **MA:** served live by mass.gov (every file `origin: live`).
- **NV:** fetched `WARN_2021.pdf` live, and its hash matched `fetch/custom/nv.TRANSCRIPTIONS`.
- **NE:** `ne.csv` is byte-identical to `2026-09-30-ne-ndol-reports/ne.csv`.
- **KS:** re-captured all 910 portal IDs. `ks.csv` is byte-identical to the frozen file.
- **DC:** re-fetched with the new `fetch/patches/dc.py`. DOES moved its year links out of the first `div.field-items`, which now holds only the sidebar's relative Rapid Response links. The patch finds year links by their text and resolves them against the page. It returned 149 rows (frozen: 140), all verification checks passed, every frozen DC key is kept, and 9 rows are new.

### 2. Which fresh captures replace frozen ones

A single-capture bundle replays one `raw/<xx>.csv` per state. A fresh capture
that no longer lists a row from the frozen capture would therefore drop a
released notice. Each fresh file was normalized next to its frozen file
(scratch `compare_captures.py`). A fresh capture replaced the frozen one only
when it met all four conditions:

- (a) it fetched and verified;
- (b) it passed `validate_agency_raw_file`;
- (c) it is not pinned to an agency artifact;
- (d) it keeps every frozen dedupe key.

NE and NV are the required exceptions to (d): NE re-keys by design (through its
transition map), and NV's frozen file fails the new `expected_columns`.

| State | Fresh file used | Reason when frozen kept |
|---|---|---|
| AL CA CO FL ID IN MI MT NC ND NJ NM OK PA SD UT VT WA WI | yes | |
| DC | yes (patched collector; +9 rows, no frozen key lost) | |
| NE | yes | |
| NV | yes | |
| KS | yes (identical bytes; new companion evidence) | |
| AK MS RI | frozen = fresh (identical bytes) | |
| MO | no | The fresh `mo.csv` reaches back to 2019 and overlaps `agency/mo_annual`. Replayed, it displaced 281 released MO keys (first build, attempt 1). |
| TX | no | `raw/tx.csv` is pinned to `agency/tx/manifest.json` (`raw_csv_sha256`), so a fresh file fails `tx_source.read_artifacts`. |
| IL | no | `raw/il.csv` must equal `agency/il/il.csv`, and `il_bundle` refuses a bundle that already has `agency/il`. |
| SC | no | The replay reads the `cache/sc` PDFs, not `raw/sc.csv`. |
| HI | no | The new collector format re-keys all 469 frozen keys (393 fresh keys, none shared). |
| MN | no | Rolling window: the fresh page lists 68 rows (2026 only), and 919 frozen keys are absent. |
| AZ, DE, ME | no | Rolling JobLink search results: 99, 35 and 5 frozen keys absent. |
| CT, NY, OH | no | Rolling lists (rows removed or re-keyed): 3, 5 and 3 frozen keys absent. |
| MA, MD | no | One frozen key absent each. MA's Sodexo/Bentley row was removed; MD ID 456199 "Kirson Medial" was corrected to "Medical". |
| VA | no | The fetch failed (above). |
| GA IA KY LA OR TN | not scraped | Archive status; the agency artifacts in the bundle are unchanged. |

### 3. Archive additions

- **WI:** `fetch_wi_dwd` fetched DWD's 2016–2019 year pages live on 2026-09-30:
  `archives/wi/dwd-{2016..2019}.htm`, with `.json`/`.url` sidecars (321 rows, 0 failures).
- **NE:** files were copied from `data/source_snapshots/2026-09-30-ne-ndol-reports/archives/ne/`:
  - the pinned 2025-03-23 Wayback capture that `fetch_ne_dol` reads (29 rows dated 2020–2022);
  - the 20 year reports;
  - the collector's WARN page capture `warn-page-20260930.html`.
- All 77 files are under `backfill/cache/archives/{wi,ne}/` and are listed in
  the manifest's `archive_additions` (the scratch directory `additions/archives/`).

### 4. Bundle build

```bash
python -m warnlive.migrate.source_bundle verify data/source_snapshots/2026-09-24-strict-ks-ky-source-bundle.tar.gz
# one overlay per state, each from the previous intermediate (deleted after use), in this order:
for s in al ca co fl id in mi mt nc nd ne nj nm nv ok pa sd ut vt wa wi dc; do
  python -m warnlive.migrate.source_bundle overlay-raw <prev> --raw-file <fresh>/raw/$s.csv --out <next>
done                      # dc.csv from <fresh-dc>/raw/
python -m warnlive.migrate.source_bundle overlay-raw <prev> --raw-file <fresh>/raw/ks.csv \
  --evidence-archive data/source_snapshots/2026-09-30-ks-portal-evidence.tar.gz --out <next>
python -m warnlive.migrate.source_bundle add-archives <prev> --archives <additions> \
  --out data/source_snapshots/2026-09-30-v1.2-source-bundle.tar.gz
python -m warnlive.migrate.source_bundle verify data/source_snapshots/2026-09-30-v1.2-source-bundle.tar.gz
```

- The overlay order is the first build's, with DC appended.
- `add-archives` is the tested subcommand that replaces the scratch `add_archives.py`.
- Against the first build's bundle, the manifests differ only in `raw/dc.csv`
  and in `raw_overrides` (which now lists it). Every other member has the same
  SHA-256, so the new subcommand reproduced the scratch helper's output.
- `raw_overrides` is cumulative: it still lists `raw/il.csv` from the v1.1.0 chain.

### 5. Replay and outputs

```bash
python -m warnlive.migrate.offline_rebuild \
  --bundle data/source_snapshots/2026-09-30-v1.2-source-bundle.tar.gz \
  --quality-evidence-dir data/source_snapshots/2026-09-24-quality-evidence \
  --db <run1>/v12.sqlite --observed-at 2026-09-30 --source-only \
  --report <run1>/report.json --exceptions <run1>/v12.exceptions.jsonl
# the same command again into <run2>/ (independent temp directories)
cp <run1>/v12.sqlite <out>/db/warn.sqlite
warnlive export --db <out>/db/warn.sqlite --data-dir <out>/data
warnlive build-site --db <out>/db/warn.sqlite --out <out>/site --as-of 2026-09-30
```

To separate policy from captures, the frozen v1.1.1 bundle was also replayed
with the same code (`--observed-at 2026-09-24`; report and ledger in scratch).
That replay gave 79,687 notices, 94,037 versions and 8,076,135 workers.

## Code changes since the first build (`aa7a0f4`, `500f6c3`)

1. **NE 2020–2022 archive:** `offline_rebuild._cached_agencies` now runs
   `fetch_ne_dol` (cache-only), as the live `backfill-archives` does.
   - An archive row whose key already exists is held
     (`matched_existing_key_not_admitted`), and one whose employer and month
     already exist is held (`possible_employer_month_overlap`), so nothing is
     ingested twice.
   - The pinned capture yields 29 rows, and all 29 were admitted.
2. **NY dashboard rows:** the cause was not the multi-site refactor.
   - `control_number_versions` now admits NY control-number filings that
     v1.1.1 held as `conflicting_same_key`. Those filings became loose
     name/date candidates for dashboard rows that v1.1.1 admitted alone.
   - (a) A dashboard row that matches exactly one such filing is that filing's
     version. The 24 released keys retired this way are in
     `data/review/ny-dashboard-correspondence-transition-2026-09-30.csv`
     (`migrate/ny_correspondence_transition.py`).
   - (b) A loose correspondence holds a dashboard row only when some candidate
     is an ordinary notice. When every candidate is a filing rebuilt from
     conflicting detail pages, the row stays admitted and records
     `source_details.possible_correspondence`.
   - (c) Such a recovered filing, unless it is an exact version target, is
     held as `possible_duplicate_of_admitted_notice`, so the published
     dashboard notice stays the single record.
3. **NE observation pointers:** these now name the bundle path
   `backfill/cache/archives/ne/<report>-<year>.html`. The rebuild fails if any
   observation names a file that is not a bundle member.
4. **DC:** new collector patch `fetch/patches/dc.py` (above).
5. **`source_bundle add-archives`:** new tested subcommand; the README's
   rebuild section documents it.
6. **CA entries (review item A):** distinct same-key rows are separate entries
   only within one document.
   - `same_key_document_fields` names each row's document: CA uses the EDD
     report's `source_file` or the annual PDF's `year_file`; NE uses the NDOL page.
   - Same-key rows from different fiscal-year reports are one notice's
     versions (for example Broadcom/Avago Irvine 2016-01-29: 689, then 771).
7. **IA amendments (review item B):**
   - Each version builds on the notice's latest version.
   - An amendment's count is applied only when its type states a restated
     total; no event-log type does.
   - Its date is applied only for "Change in date".
   - Every amendment is listed in `amendments[]`, and a notice with more than
     one amendment has `worker_allocation: "unresolved"`.
8. **OR (review item C):** rows that repeat the same site (Company Name and
   Location) and date are not summed. The notice's workers are left unknown,
   with `worker_allocation: "unresolved"`. The reviewer's WARN# 8617 example
   is two sites (Bennett St. and Walker Rd), so it is summed as before.

## Results

| | v1.1.1 | v1.1.1 bundle, new code | First build (superseded) | This candidate |
|---|---:|---:|---:|---:|
| Notices | 74,828 | 79,687 | 80,773 | 80,703 |
| Versions | 88,748 | 94,037 | 95,090 | 95,089 |
| Reported workers | 7,815,994 | 8,076,135 | 8,212,376 | 8,202,435 |
| Links | 0 | 1,969 | 1,969 | 1,969 |
| Ledger rows | 9,668 | 6,132 | 5,665 | 5,702 |
| Source observations | 1,009 | | 3,135 | 3,135 |

- **Candidate fingerprints:** notices
  `887a9bd2f9687dc1a6f0f5974441564aba7428c85ae5a83dc66ea4d1edc4bf5d`, versions
  `68be84b413dd0a621bcf5e0d7acd28f827bfd83afc614276f21e63b1d16c6b0a`, links
  `d9344a4ecd2471db9bc14925be8bd2b8e46145a7abf758ca0d93ecec53224195`.
- **Row accounting:** the replay reports no unaccounted rows in any layer. The
  agency cache has 387 excluded rows and the current raw layer 2,218, all in the ledger.
- **Observations:** 825 NE `not_in_agency_warn_report`, 1,499
  `identity_unresolved`, 803 admitted, and 8 LA event_unresolved, rescinded or
  annotation. All 15 observation artifacts are bundle members.

In the table below, "Policy" is the old-bundle replay minus v1.1.1, and
"Captures/archives" is the candidate minus the old-bundle replay. NE and NV
show −933/−296 under policy only because the old files fail the new
`expected_columns` (every row is held as `verification_failed`); that is not a
policy outcome. States not listed are unchanged in notices and workers.

| State | v1.1.1 | Policy Δ | Captures/archives Δ | Candidate | Δ vs first build | Workers v1.1.1 → cand. |
|---|---:|---:|---:|---:|---:|---:|
| AL | 1,031 | 0 | +7 | 1,038 | 0 | 178,843 → 179,252 |
| CA | 25,176 | +3,283 | +34 | 28,493 | −107 | 2,167,497 → 2,328,500 |
| CO | 827 | 0 | +17 | 844 | 0 | 84,855 → 86,310 |
| DC | 140 | 0 | +9 | 149 | +9 | 24,824 → 26,764 |
| FL | 5,435 | 0 | +16 | 5,451 | 0 | 646,598 → 639,851 |
| HI | 452 | +17 | 0 | 469 | 0 | 0 → 0 |
| IA | 399 | +161 | 0 | 560 | 0 | 29,444 → 35,882 |
| ID | 193 | 0 | +2 | 195 | 0 | 21,779 → 21,828 |
| IL | 4,890 | −2 | 0 | 4,888 | 0 | 674,447 → 674,281 |
| IN | 1,011 | 0 | +5 | 1,016 | 0 | 152,045 → 152,653 |
| MI | 100 | +7 | +9 | 116 | 0 | 11,994 → 14,726 |
| MT | 42 | 0 | +2 | 44 | 0 | 3,820 → 3,838 |
| NC | 1,197 | −6 | +7 | 1,198 | 0 | 122,249 → 122,866 |
| NE | 933 | (−933) | (+164) | 164 | +29 | 41,342 → 25,546 |
| NJ | 2,327 | −1 | +2 | 2,328 | 0 | 350,533 → 350,712 |
| NM | 115 | +1 | +2 | 118 | 0 | 14,842 → 15,170 |
| NV | 296 | (−296) | (+324) | 324 | 0 | 35,892 → 44,672 |
| NY | 6,315 | +2,492 | 0 | 8,807 | −1 | 606,499 → 754,396 |
| OK | 200 | 0 | +2 | 202 | 0 | 0 → 0 |
| OR | 877 | +106 | 0 | 983 | 0 | 154,600 → 177,930 |
| PA | 289 | +6 | +17 | 312 | 0 | 42,979 → 45,804 |
| RI | 125 | +1 | 0 | 126 | 0 | 13,765 → 14,794 |
| SD | 79 | 0 | +1 | 80 | 0 | 8,758 → 8,758 |
| UT | 280 | 0 | +3 | 283 | 0 | 35,677 → 36,878 |
| VT | 29 | 0 | +20 | 49 | 0 | 1,340 → 2,487 |
| WA | 1,456 | 0 | +47 | 1,503 | 0 | 232,029 → 246,858 |
| WI | 3,242 | +23 | +326 | 3,591 | 0 | 293,476 → 325,812 |

Notes on the deltas:

- **CA:** 107 fewer notices than the first build. Same-key rows in different
  EDD fiscal-year reports now fold into versions: 110 key groups span
  documents. Five of those groups, 10 rows, move the action date by more than
  45 days, so ingest holds them as `suspected_same_key_collision`. None of
  those keys was released. CA versions fell by only 12 (34,711 → 34,699)
  because the folded rows became versions.
- **NY:** 1 fewer notice than the first build. 23 released dashboard rows are
  admitted again (the first build held them), and 24 recovered control-number
  filings are held as `possible_duplicate_of_admitted_notice` (50 detail-page
  rows; Cecilware's dashboard row matches two filings). 23 dashboard notices
  carry `possible_correspondence`.
- **NE:** 164 = 135 from `raw/ne.csv` + 29 from the 2020–2022 archive. The
  135 are 83 kept, 47 re-keyed, and 5 not previously published: the Fortrex
  2026-08-26 page row, plus the second rows of same-day WARN report filings
  for Schreiber 2012, Skag-Way 2015, Michael Foods 2015 and Hayneedle 2019,
  which `same_key_policy: distinct_rows` keeps as separate entries within one
  year page.
- **IA:** notice count unchanged. 30 notices change against the first build:
  28 worker counts and 25 action dates, for net +1,719 workers (for example,
  MetLife goes back from 3 to 161). 10 notices are marked
  `worker_allocation: "unresolved"`.
- **OR:** 4 historical-workbook filings (WARN# 2251, 5587, 6271, 6469) list the
  same site and date twice. Their workers are now unknown, which is −1,182
  workers against the first build.
- **WI:** +326 is mostly the 321 DWD 2016–2019 rows. NV's +28 is 20
  transcribed 2021 rows plus 8 new rows. FL workers fall by 6,747 because
  fresh rows revise worker counts (as new versions).

## Released-key accounting

Every v1.1.1 notice key (74,828) was checked against the candidate (scratch
`accounting.py`, which now reads the NY map as well as the WI/FL and NE maps):

| Class | Keys |
|---|---:|
| Present in the candidate | 73,895 |
| Retired via `wi-fl-update-transition-2026-09-29.csv` (survivor present) | 48 |
| Retired via `ne-key-transition-2026-09-30.csv` (`retire`) | 803 |
| Re-keyed via the NE map (new key present) | 47 |
| Retired via `ny-dashboard-correspondence-transition-2026-09-30.csv` (survivor version holds the dashboard row) | 24 |
| Held by a nonnotice rule (CA 2 + IL 2 `apparent_agency_test_record`, NC 6 `nc_county_summary_count_row`, NJ 1 `employer_name_has_no_letters_review`) | 11 |
| **Unexplained** | **0** |

- **NY map check:** regenerated from this candidate
  (`python -m warnlive.migrate.ny_correspondence_transition --candidate <run1>/v12.sqlite`),
  it is byte-identical to the committed CSV. It lists 24 transitions and 0
  missing unmapped NY keys.
- **WI/FL map check:** regenerated from this candidate, it has 4 additional
  rows: Regal Beloit Clinton 2020, and Gannett Appleton 2018 (2 update rows
  each). Neither their retired keys nor their surviving keys were released;
  they come from the fresh WI captures and the DWD archive. The committed map
  covers every released retirement.
- **New keys:** 6,808. By state: CA 3,319, NY 2,516, WI 397, IA 161, OR 106,
  NE 81, WA 47, NV 28, PA 23, VT 20, HI 17, CO 17, FL 16, MI 16, DC 9, and
  fewer than 8 each in AL, NC, IN, NM, UT, ID, MT, NJ, OK, RI and SD.

## Exception ledger by reason

| Reason | v1.1.1 | First build | Candidate |
|---|---:|---:|---:|
| coalesced_same_key_content | 1,046 | 1,322 | 1,322 |
| unreviewed_rapid_response_row | 1,284 | 1,284 | 1,284 |
| ne_layoff_closure_report_not_warn | 0 | 825 | 825 |
| multi_site_filing_identity_unresolved | 3,023 | 571 | 571 |
| later_capture_of_existing_warn_number | 307 | 305 | 305 |
| existing_notice_event_correspondence_unresolved | 671 | 220 | 195 |
| nc_county_summary_count_row | 0 | 188 | 188 |
| possible_revision_same_event | 126 | 134 | 136 |
| amendment_without_verified_parent | 263 | 110 | 110 |
| duplicate_agency_capture | 62 | 98 | 98 |
| folded_into_filing_phases | 0 | 91 | 91 |
| newer_agency_capture_overlap | 80 | 80 | 80 |
| amendment_parent_ambiguous | 0 | 63 | 63 |
| incomplete_historical_row | 58 | 58 | 58 |
| duplicate_source_observation | 50 | 50 | 50 |
| possible_duplicate_of_admitted_notice | 0 | 0 | 50 |
| suspected_same_key_collision | 573 | 36 | 46 |
| multi_site_or_phase_identity_unresolved | 427 | 34 | 34 |
| pdf_table_text_unresolved | 33 | 33 | 33 |
| pdf_table_row_unresolved | 32 | 32 | 32 |
| conflicting_same_key | 1,332 | 25 | 25 |
| superseding_amendment_observation | 25 | 25 | 25 |
| All other reasons (v1.1.1: 25 reasons, largest `site_phase_or_worker_allocation_unresolved` 181; builds: 27 reasons, each ≤ 11 rows) | 276 | 81 | 81 |
| **Total** | **9,668** | **5,665** | **5,702** |

- The 195 `existing_notice_event_correspondence_unresolved` rows are exactly
  the rows v1.1.1 held for that reason whose candidate is an ordinary notice.
- The 2 extra `possible_revision_same_event` rows are NY dashboard rows that
  v1.1.1 held for the same reason. With only recovered candidates, they fall
  through to that hold.

## Checks

| Check | Outcome |
|---|---|
| `pytest tests/ -q` at `500f6c3` (main checkout) | 672 passed, 1 skipped |
| `source_bundle verify` (base and new bundle) | pass |
| Replay: SQLite integrity, foreign keys | `ok`, 0 |
| Replay: unaccounted rows in every layer | 0 |
| Second independent replay | Fingerprints, notices (80,703), versions (95,089), workers (8,202,435) and links are identical. The exception ledgers are byte-identical (SHA-256 `af53640f…6c1d92`). The reports differ only in temporary-directory paths (`exceptions.path` and `raw/*/verification/checks[fetch_ok].detail`). |
| Released-key accounting | 0 unexplained |
| Export and database agreement | 80,703 national CSV rows = sum of 47 state CSVs = database notices. Workers are 8,202,435 in both. Site `meta.json` totals are 80,703 notices and 8,202,435 workers. 50 CSVs, 568 site JSON files. |
| Observation pointers | All 3,135 exported observations name one of 15 artifacts, all of them bundle members (NE: `backfill/cache/archives/ne/layoff_closure_report-<year>.html`). |
| `warnlive check-kansas-holds` (candidate copy) | no publication-blocking Kansas conflict |
| `npm test --prefix site` | 2 files, 3 tests passed. Run in the worktree with `site/public/data` linked to the candidate site payload; the tests do not read the payload. |
| `npm run build --prefix site` | built (the usual chunk-size warning), with the candidate payload as `public/data` |
| `warnlive check-regressions` against `data/health/snapshot.json` (v1.1.1; read-only copy) | **FAILED**, explained below |

Regression-check failures:

- **`state_notice_counts`: NE 933 → 164.** Intended. 803 released NE keys were
  NDOL layoff/closure-report rows, now held as
  `ne_layoff_closure_report_not_warn` and exported as observations. 47 were
  re-keyed (NE map), and 29 rows dated 2020–2022 were added.
- **`field_emptied`: IA `no_location` 99%, MS `no_location` 86%.** These fields
  were filled, not emptied. IA went from 399 of 399 notices without a location
  to 3 of 560; MS went from 129 of 142 to 7 of 142, from the IA and MS location
  fixes. The check flags movement in either direction.
- **Warning, `employer_counts`:** NE 653 → 137 (the NE report split) and NY
  5,306 → 6,373 (dashboard rows recovered by the 19849ad policy).
- The snapshot must be refreshed in the release commit. It was not written here.

## Superseded build (first attempt under this date)

- **Code and bundle:** commit `0fc58fd`; bundle SHA-256
  `160c9dec71a1a9ffcd3f60aec691eef4beddd38b7d21056e13ff94abf3daa5fd` (the same
  inputs without fresh DC); report SHA-256 `d465f65a…c8ef5ef5`; ledger (gzip)
  `68538e1e…728514e`.
- **Result:** 80,773 notices, 95,090 versions, 8,212,376 workers; fingerprints
  notices `466caaf6…2e18db3`, versions `fb3973ec…bdbc09`.
- **Blockers found:**
  - 47 NY released keys were unexplained.
  - The NE 2020–2022 archive was not replayed.
  - NE observation pointers did not resolve.
  - DC failed.
  - `add_archives.py` was a scratch helper.
  - Independent review found: CA same-key rows split across fiscal-year
    reports (item A); IA amendment counts and dates applied from the original
    record (B); OR same-site/date rows summed (C).
- **Attempt 1 within that build** also overlaid `raw/mo.csv` (SHA-256
  `e3ec8366…89cbcd`, since deleted) and lost 281 released MO keys. MO was then
  excluded.

## Unresolved problems and decisions needed

1. **Single-capture bundles lose history from rolling sources.** The MN, AZ,
   DE, ME, CT, NY, OH, MA and MD fresh captures drop rows that v1.1.1
   admitted, and HI's new collector re-keys all of its rows. Keeping the
   frozen files keeps the released keys but leaves out their new rows (more
   than 900 MN rows alone). This is an architecture decision: bundles with
   several dated captures per state, or transition maps per state.
2. **TX, IL and MO fresh captures cannot enter this bundle.** TX and IL are
   pinned to agency artifacts, and MO's fresh file overlaps `agency/mo_annual`.
3. **VA** needs Xvfb on this Mac; whether the CI runner has it was not checked.
4. **CA cross-report collisions.** 5 same-key groups whose two fiscal-year
   rows disagree on the action date by more than 45 days are held (10 rows).
   It is unresolved whether they are revisions or separate events.
5. **NY possible duplicates.** 24 recovered filings are held beside 23
   dashboard notices that carry `possible_correspondence`. Merging them needs
   source evidence or a reviewed decision.
6. **OR historical workbook.** The 4 filings whose rows repeat a site and date
   now have unknown workers.
7. **Independent review.** This build was reviewed (see "Final build"); its
   one blocker is fixed in `abac0db`.
8. **Release commit.** The regression snapshot and the `test.yml` replay
   baseline must be updated in the commit that promotes a release.
