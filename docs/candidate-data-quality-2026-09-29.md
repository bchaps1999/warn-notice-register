# Data-quality candidate (2026-09-29)

**Status: candidate, not a release.** This replays the frozen v1.1.1 sources
with the parsing, address and geography fixes on branch `data-quality-fixes`.
The published register remains v1.1.1 until a release is cut from this work.

## What ran

- Code: commit `2068122` (branch `data-quality-fixes`), in an isolated worktree.
- Bundle: `data/source_snapshots/2026-09-24-strict-ks-ky-source-bundle.tar.gz`,
  SHA-256 `746745a066cb5e3c6ad5fc4263b83b27b3bba4a0d25115d46d78341e1df42070`,
  with `--quality-evidence-dir data/source_snapshots/2026-09-24-quality-evidence`.
- Command (outputs in the session scratchpad, not in `data/`):

  ```bash
  python -m warnlive.migrate.offline_rebuild \
    --bundle data/source_snapshots/2026-09-24-strict-ks-ky-source-bundle.tar.gz \
    --quality-evidence-dir data/source_snapshots/2026-09-24-quality-evidence \
    --db <tmp>/cand.sqlite --observed-at 2026-09-24 --source-only \
    --report <tmp>/report.json --exceptions <tmp>/exceptions.jsonl
  ```

- Checks: full test suite (512 passed, 1 skipped); integrity `ok`, 0 foreign-key
  errors; every released `dedupe_key` compared against the candidate;
  independent review of commit `f7a1ed9` against an earlier candidate. The
  review's three defects are fixed in `0b46307`.

## Result against v1.1.1

| | v1.1.1 | Candidate |
|---|---:|---:|
| Notices | 74,828 | 74,836 |
| Versions | 88,748 | 88,760 |
| Reported workers | 7,815,994 | 7,823,761 |
| Exception ledger rows | 9,668 | 9,660 |
| Rows with `site_address` | 6,469 | 16,293 |

Notice changes by state: HI +17 (starred amendment lines recovered; undated
lines key on their PDF), NM +1 and RI +1 (rows previously dropped for one
unparseable date), CA −2 and IL −2 (agency test records), NC −6 (county-summary
count rows admitted as employers "0"–"6"), NJ −1 (employer "1961", no letters).
Every other released `dedupe_key` is unchanged.

Ledger reason changes: `parse_failure` 24 → 2; new holds
`nc_county_summary_count_row` 188 (179 of them were previously coalesced into
the six NC junk notices), `apparent_agency_test_record` 4,
`employer_name_has_no_letters_review` 1; `coalesced_same_key_content`
1,046 → 867 (NC only).

Field changes without key changes include:
- NV: 31 rows re-read under their printed column labels.
- MS: about 118 rows now carry `City (County)` as `location`.
- IA: 399 rows get their location and layoff type.
- Upstream date corrections that invented dates are nulled or replaced by the
  date written in the cell (NJ Morgan Stanley 2024 → 2023; AL `01/01/0001` → null).

Addresses: `site_address` is recomputed by one role policy in both the live
scrape and the rebuild (`warnlive/enrich/site_address.py`), with a new
`site_address_basis` column. NY 4,901, IL 4,358, CA 4,313, MD 1,253, NC 907,
PA 225, SC 172 and GA 164 rows have it. Geography gains about 1,400 resolved
rows from place aliases (LA communities, NYC boroughs) and the EDD report
county fallback.

## Open before release

1. **Published notices now held.** The live database still holds the 11
   notices listed above. The live scrape holds their source rows before
   ingest, so it will neither refresh nor remove them. A release must either
   publish a fresh replay or explicitly retire them. Retiring them lowers the
   notice count, so the regression snapshot has to be refreshed in the same
   commit.
2. **Fresh sources.** Most captures in this bundle date from July 2026. A
   release should freeze a new bundle that includes current captures and the
   Wisconsin 2016–2019 DWD pages (321 rows; see `fetch_wi_dwd` in
   `warnlive/backfill/state_archives.py`).
3. **Replay CI baseline.** `test.yml` compares the replay with the v1.1.1
   report and ledger byte for byte. Update it to the new release report in
   the commit that promotes the release.
4. **Policy decisions pending.**
   - Florida company-cell addresses (about 2,550) and other unlabeled portal
     addresses are held out of `site_address`.
   - Michigan's 136 upstream dates transcribed from linked PDFs are kept, with
     a parse note.
   - West Virginia stays `unverified` until its 28 listing documents held for
     review are checked.
5. **Day-dependent windows.** Live runs measure the future-date window and
   the correction audit from the scrape date; replays measure them from the
   bundle date.
