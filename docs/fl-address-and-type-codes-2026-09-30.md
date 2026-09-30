# Florida site addresses and DC / MD / WI type codes (2026-09-30)

Two evidence-backed mappings added on branch `data-quality-fixes`. Both
change derived or evidence fields only; no source row is added, dropped or
rewritten, and the raw cells stay in `raw_extra`.

## 1. Florida company cell as a site address

Florida's `Company Name` cell is the company name, one or more street lines,
and a final uppercase `CITY, FL, ZIP` line. The column does not label the
role of the street line, so `site_address` previously left it out
(`not_surfaced_unlabeled_role`).

### Sampling method

A 52-row sample was checked against independent evidence (verified 2026-09-29,
sample file `fl_address_role_sample.csv` in the session scratchpad; raw row
index, cell, classification, evidence type and URL per row). Strata:

- `street_in_FL`, 2019-2026 layout: seeded draw (seed 20260929) of one
  small (<100 workers) and one large (>=100) notice per year, plus one extra in
  2020, 2023, 2024 and 2025; 20 rows. Evidence: the FloridaCommerce notice
  letter attached to the row (reactwarn.floridajobs.org `DownloadAzureFile`).
- `street_in_FL`, 2015-2018 PDF era: 16 rows; evidence from news reports,
  location lists, aggregators or the cell itself, since no letters are
  attached. Weaker evidence.
- `street_out_of_state` (street lines name another state or a non-FL ZIP; 164
  rows, all 2023 or later): 16 rows with distinct employers, each checked
  against its notice letter.

### Results

| Stratum | Rows | Affected site | Ambiguous | HQ / mailing |
|---|---:|---:|---:|---:|
| Street in Florida, 2019-2026 (letter-verified) | 20 | 19 | 1 | 0 |
| Street in Florida, 2015-2018 | 16 | 11 | 5 | 0 |
| Street out of state, 2023+ | 16 | 0 | 0 | 16 |
| Total | 52 | 30 | 6 | 16 |

Every decidable in-state row (30 of 30) was the affected worksite; none was a
headquarters. Every out-of-state street line (16 of 16) was the headquarters
or reporting office of remote or out-of-state employees. Several letters list
more than one site while the cell shows one of them, so the in-state street
line is a worksite of the notice, not necessarily the only one.

### Rule adopted

`warnlive/enrich/site_address.py` basis `fl_company_cell_in_state`:

- Applies to the three-part cell (name, street line(s), final uppercase
  `CITY, FL, ZIP`). The older mixed-case layout (2015-2018, city and ZIP on a
  wrapped line) and the pre-2015 inline layout are not accepted and stay
  `not_surfaced_unlabeled_role`.
- Street lines that name another state (state plus digits, `, XX`, or a state
  name closing a comma-separated part) are rejected as `names_another_state`;
  a ZIP outside 32000-34999 in the final line is rejected as
  `zip_not_florida`. These rows are left blank and are not relabeled as
  `employer_mailing_address`.
- Accepted values must also pass the existing shape checks: one street
  address, a house number, no P.O. box, remote or multiple-site wording.
- The exported value is the street part plus the cell's city, state and ZIP
  (`404 Duval Street, KEY WEST, FL 33040`).
- The 2015-2018 stratum had weaker evidence (11 of 16 decidable-as-worksite,
  5 ambiguous, 0 headquarters) and a different layout, so it is not included.

Measured on the candidate database copy
(`scratchpad/cand2/cand.sqlite`, `apply(dry_run=True)`, FL only):

| Outcome | Rows |
|---|---:|
| surfaced | 1980 (365 after a venue or facility line) |
| rejected: names_another_state | 167 |
| rejected: not_a_street_address | 106 |
| rejected: not_surfaced_unlabeled_role (other layouts) | 398 |
| rejected: multiple_addresses_in_field | 7 |
| rejected: zip_not_florida | 5 |
| left to quality evidence | 5 |

## 2. DC, Maryland and Wisconsin type codes

Legends as published; retrieved 2026-09-30 (DC and Maryland pages saved in
the session scratchpad; the Wisconsin page returned HTTP 403 to the saved
fetch, so its text is transcribed from the retrieval that supplied this
mapping and should be re-checked in a browser).

### DC, `Code Type`

Source: https://does.dc.gov/page/industry-closings-and-layoffs-warn-notifications

> Legend: 1 = Layoff • 2 = Permanent Closures

Mapping: 1 -> `mass_layoff`, 2 -> `closure`.

### Maryland, `Type`

Source: https://labor.maryland.gov/employment/warn2011.shtml through
`warn2022.shtml` (legend beside each year's log; the current `warn.shtml`
page carries no legend).

> Type Codes 1. PLANT CLOSURE 2. MASS LAYOFF

Mapping: 1 -> `closure`, 2 -> `mass_layoff` (reversed relative to DC). A
numeric cell with a note ("2 Temporary", "1 *Note-...") maps on the leading
digit only when it is exactly 1 or 2 not followed by another digit;
`is_temporary` is set to 1 only when the cell says "temporary". From 2023 the
column is free text; only the exact phrases "Mass Layoff", "Mass Layoff - No
Recall" and "Plant Closure" are read there (the generic engine already reads
most variants); others stay unknown.

### Wisconsin, `Original Notice Type / Update Type`

Source: https://dwd.wisconsin.gov/dislocatedworker/warn/

> CL Facility Closure, WR Workforce Reduction; Update Types AW Change to
> Number of Affected Workers, LS Change to Layoff Schedule, OC Other Change,
> RN Rescission of Notice

Mapping: the original type token CL -> `closure`, WR -> `mass_layoff`; a cell
listing both ("CL, WR") stays unknown. Update tokens (AW, LS, OC, RN) are
recorded in `source_details.update_types`; a rescission (RN) does not change
admission.

### Recorded evidence and measured effect

`source_details.layoff_type_evidence` holds `rule` (`source_type_code_v1`),
`source_field`, `source_text` (the raw cell), `type_code` and
`type_code_legend_url`. Codes only fill a `layoff_type` the engine left
unknown. Re-normalizing the raw files of
`data/source_snapshots/2026-09-24-strict-ks-ky-source-bundle.tar.gz`
(`normalize_file`, observed 2026-09-24):

| State | Rows | unknown before | unknown after |
|---|---:|---:|---:|
| DC | 140 | 140 | 0 |
| MD | 1401 | 1007 | 5 |
| WI | 626 | 626 | 4 |

Remaining unknown: MD "Temporary Furlough", "Temporary", "Unsure at this
time", "The company is idling the plant...", "N/A"; WI "CL, WR" (3) and
"Unknown" (1). MD `is_temporary` gains 2 rows ("2 Temporary", "2 NOTE:
Currently a temporary lay-off situation"). One MD cell, "2 (Possibly turning
into a closure)", is read `closure` by the generic engine (the word
"closure") although the legend code 2 means mass layoff; codes never override
an existing reading, so it is left as is and flagged for a decision.
Adding `update_types` to WI `source_details` changes those rows' record hash,
as does the new `layoff_type`.

## Follow-up

FloridaCommerce publishes the notice-letter PDF for every 2019+ row
(reactwarn.floridajobs.org `WarnList/DownloadAzureFile?file=...`). Pinning
those letters through the quality-evidence pass would replace this sampled
inference with document evidence for the affected worksite, including the
multi-site notices and the out-of-state-street rows (whose Florida sites are
named in the letters).
