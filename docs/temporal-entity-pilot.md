# Temporal entity pilot

This pilot asks which legal entity filed a WARN notice and what a dated source supports about its corporate relationships **at the notice date and at the effective date or dates**. It is a review sidecar. It does not change `notices`, the published CSV, the site, or existing `parent_company` and CIK annotations.

## Inputs and decisions

- `data/reference/temporal/sources/candidate_notices_2026-09-23.csv` freezes nine rows selected from the read-only candidate SQLite file `/private/tmp/warn-date-fixed-ny-reviewed-rerun-20260923.sqlite`. `selection.json` contains their stable `(state, source_notice_id)` keys. The excerpt preserves candidate dates exactly, including missing dates and precision.
- `data/reference/temporal/sources/` pins the official SEC, New York, FirstGroup, and Transdev documents consulted here. `ledger.json` records each file's SHA-256, original URL, retrieval date, and a source locator for each relationship decision.
- `ledger.json` has *scoped identity assertions*: a reviewed link from one filed name in one notice to a legal entity. A similar name or brand elsewhere does not inherit that decision. A CIK identifies an SEC registrant; it does not establish the employer's parent.
- Relationship assertions have a specific type and either one exact as-of date (`snapshot`) or an explicitly supported closed interval (`interval`). A merger announcement is not the closing date. An acquisition snapshot is not assumed to remain true forever. Two snapshots with the same parent do not by themselves prove every intervening date.
- These are provisional agent-reviewed pilot assertions, not human approvals. Human or domain review is required before production promotion.
- The resolver must return `unknown` or `identity_unresolved` when evidence does not cover a date. Conflicting assertions for the same relationship/date must remain a conflict for review.

The pilot reconstructs historical relationships using sources available now. It does not answer what a researcher could have known on the WARN date; that separate question would filter on source publication and retrieval dates.

The [2013 Sprint 10-K](https://www.sec.gov/Archives/edgar/data/101830/000010183014000012/sprintcorp201310-k.htm) distinguishes the Delaware Sprint Corporation incorporated in 2012 from the Kansas predecessor later named Sprint Nextel/Sprint Communications. The ledger keeps them as separate legal entities even though the SEC reporting CIK carried across succession.

The relationships in this first ledger are deliberately sparse: the Delaware Sprint Corporation is a wholly owned **indirect** T-Mobile subsidiary at the April 1, 2020 merger closing, based on its [SEC 8-K](https://www.sec.gov/Archives/edgar/data/1283699/000119312520093622/d886127d8k.htm). Transdev North America acquired 100% of First Transit Inc. stock on March 6, 2023, according to [Transdev's 2023 financial report](https://www.transdev.com/uploads/2026/02/2023-transdev-financial-report-2023.pdf), Note VI.1.1. The [New York First Transit notice](https://dol.ny.gov/system/files/documents/2024/03/warn-first-transit-transdev-north-america-western-2023-0298-3-27-2024.pdf) explicitly names First Transit Inc. as a Transdev North America subsidiary on March 21, 2024; it does not establish whether that parent is direct or indirect. [FirstGroup](https://www.firstgroupplc.com/investors/information-for-shareholders/proposed-sale-of-first-student-and-first-transit.aspx) says it completed the sale of First Transit and First Student to EQT Infrastructure on July 21, 2021; this is pinned as chronology, but the pilot does not turn the transaction statement into an unverified direct legal-parent interval.

## Why these notices

The selected cases cover an old SEC registrant (Sprint Corporation), a later change of control, a generic operating name (First Transit), a notice with an explicit subsidiary statement, an exact company name (The Coca-Cola Company), and a distinct Coca-Cola bottler. The generic First Transit and bottler names remain unresolved until notice-specific legal identity evidence is attached. There is no global name-to-parent promotion.

The current `data/exports/warn_notices.csv` labels the October 17, 2001 Kansas Sprint Corporation record with `parent_company=T-Mobile US`. That is historically unsupported: the SEC 8-K dates the merger's completion to April 1, 2020. This pilot identifies the 2001 Kansas predecessor separately and does not claim an alternative parent for that notice; its temporal relationship remains `unknown` until dated evidence establishes one. The published annotation is not automatically overwritten.

## Important date discrepancy

The pilot's frozen candidate has New York First Transit (`source_notice_id=374c4eb61056e8e785b6e49092109e88a1a2c9f48d1a85210d6dc677`) at `notice_date=2024-03-27` with no effective date. The [state PDF](https://dol.ny.gov/system/files/documents/2024/03/warn-first-transit-transdev-north-america-western-2023-0298-3-27-2024.pdf) instead says **Date of Notice: March 21, 2024** and **Closure Start Date = Closure End Date: June 30, 2024**. March 27 is the frozen candidate value and the PDF filename date; the reviewed dashboard row lists March 22 as its posting date. Neither is the filing's labeled notice date. The sidecar reports lookups against the frozen candidate dates as stored and does not silently repair them. The ledger's March 21 parent snapshot is therefore not selected for the old March 27 as-of lookup. This row is recorded in `data/reference/temporal/date_discrepancies.csv`; a later isolated source-only candidate has replayed the reviewed March 21/June 30–June 30 repair with explicit day/reported evidence.

In this candidate, 84,120 of 86,388 notices have a null `notice_date_precision`. The sidecar treats those as unverified day precision even when the stored string looks like a full date; they require a source-specific precision rule or source review.

The New Jersey First Transit row has a month-precision notice date represented as February 1, 2023. That placeholder is not an exact as-of date for ownership. The frozen pilot candidate also has no effective-date precision fields, so its effective start/end strings are flagged as unverified day precision. Later candidates have separate effective start/end precision and basis fields; the pilot now requires both explicit `day` precision and a source-supported basis for each endpoint. A relationship valid at one endpoint does not establish the entire layoff window.

## Run and interpret

```sh
python -m warnlive.enrich.temporal_pilot \
  --db /private/tmp/warn-date-fixed-ny-reviewed-rerun-20260923.sqlite \
  --ledger data/reference/temporal/ledger.json \
  --selection data/reference/temporal/selection.json \
  --output-dir output/temporal-entity-pilot
```

The command takes a consistent read-only SQLite backup snapshot, hashes those exact snapshot bytes (including committed WAL content), verifies source hashes, and writes deterministic review output plus a manifest that fingerprints the data, ledger, selection, and resolver code. Its status is a statement about evidence coverage, **not** a machine estimate of the true parent. A missing effective date yields no effective-date ownership conclusion. Date precision and candidate/source disagreements must be reviewed before using any result in an outcome model.

## Promotion path

To use this in the main export, first reconcile the source dates, then expand reviewed identity and relationship evidence across a representative state/year sample. Require exact relationship type, legally scoped child and parent, source bytes or an equivalent immutable reference, source date and assertion date, and conflict review. Compare any proposed temporal output with the existing `parent_company` without replacing that field by name matching or by the latest known parent. Keep direct legal parent, accounting parent, and ultimate parent in distinct fields. Publish an as-of notice result and separate as-of effective-start/end results only after coverage and error audits.
