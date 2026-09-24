# Date audit of the isolated WARN candidate

Audit date: 2026-09-23. This is a read-only review of
`/private/tmp/warn-ia-corrected-2.sqlite`, modified 2026-09-22 23:14 EDT,
SHA-256 `c684ec32be0bdb8e8aabae2681e26a1fc67946eb14f29e0438bb97268865cb04`.
It is an isolated source-only candidate, not `data/warn.sqlite` or a published
export. The companion [state summary](date-candidate-state-summary-2026-09-23.csv)
contains all 47 states. Counts describe this exact candidate and may change
in later replays.

## Follow-up: isolated date-fixed replay

The fixes were replayed from the same frozen bundle into
`/private/tmp/warn-date-fixed-20260923-v2.sqlite` (SHA-256
`8719fb03736b62684c916e8f7e127641d45519a020293cefef12257a04f4ef07`).
This remains an isolated candidate; the canonical database and published
exports were not replaced. It has 86,388 notices, 92,492 versions, 9,493
links, SQLite integrity `ok`, and zero foreign-key errors. The first replay
exposed a Florida archive-admission interaction: adding effective dates caused
14 distinct notices to be rejected by a month-overlap check. The check now
uses Florida's notice month when present; the second replay restored all 14.

| Check | Audited candidate | Date-fixed replay |
| --- | ---: | ---: |
| Effective starts present | 73,571 | 74,812 |
| Effective ends present | 1,345 | 2,637 |
| Notice/start pairs | 57,023 | 58,264 |
| End before start | 5 | 0 |
| Florida archive `thru` pairs captured in both scalar fields | 0 / 1,241 | 1,241 / 1,241 |
| Pennsylvania intervals with matching start/end components | 21 | 77 |
| New Jersey simple slash source dates with wrong saved month | 107 / 112 | 0 / 112 |

The 11 reversed Florida source pairs remain unset in both canonical effective
fields and retain their raw text. Colorado's five reversed ends are unset and
flagged in `source_details` for review. The Pennsylvania Bowhead and Cargill
starts now match the unambiguous source interval starts. All 77 Pennsylvania
interval rows in the replay have scalar starts and ends matching their two
stored source components. New Jersey's 2,267 month-precision notice dates
remain marked as such; 2,266 have paired dates and are excluded by the
read-only timing gate from its provisional day-granular cohort.

`python -m warnlive.verify.timing /private/tmp/warn-date-fixed-20260923-v2.sqlite
--out-dir /private/tmp/warn-date-fixed-20260923-v2-timing-final` writes state/source
quality counts and a row-level `provisional_day_timing_cohort.csv`. It reports
55,998 provisionally day-granular paired rows, six explicit intervals without
an end, zero reversed canonical ends, five quarantined reversed source ends,
and 9,941 negative notice-to-start
intervals. Null precision is currently the normal representation even for
day-formatted source dates, so this cohort is **provisional**, not a
source-verified exact-day research sample. Negative intervals are retained for
review, not clamped or removed.

New York 2022–24 remains unresolved. The 780 BLN-derived rows still lack
effective starts. The audited Iowa/Louisiana source bundle does not contain the yearly
dashboard CSVs, although local `workdir/cache/ny_reports` copies exist. A
strict normalized employer plus notice-date comparison to the local 2022–24
CSVs found 17 unique candidate matches, two ambiguous candidates, and 761
without that strict match. Recovering dates reproducibly requires freezing
those CSVs with provenance and reviewing filing, site, and amendment identity;
name-only bulk filling would be unsafe.
The later [New York reconciliation checkpoint](ny-date-reconciliation-2026-09-23.md)
freezes those CSVs in a new bundle and publishes a complete review ledger;
it does not change active New York dates.

## Method and scope

I queried all 86,388 current `notices` rows and their current-version
`fields_json`. I checked ISO date validity and ordering; compared canonical
dates with explicitly named source fields where a single source date parses
unambiguously; checked `source_details` interval and phase components; and
examined frozen raw text for the discrepancies below. This is a structural and
field-level audit, not a document-by-document verification of all filings.
Negative notice-to-layoff intervals are review signals, not proof of parser
error: some notices are retrospective.

## What the candidate currently stores

The scalar `effective_date` is intended as the first layoff/closure date.
`effective_date_end` stores a reported end of a continuous interval.
`source_details.dates` retains separate dates for lists and phases. The
normalizer distinguishes a narrowly recognized range from a list in
`warnlive/normalize/details.py`. It does not allocate workers to phases.

| Measure | Candidate rows |
| --- | ---: |
| Total notices | 86,388 |
| Notice date present | 69,630 |
| Effective start present | 73,571 |
| Both notice and effective start present | 57,023 |
| Effective end present | 1,345 |
| Effective end on or after start | 1,340 |
| Effective end before start | 5 |
| Source details present | 21,639 |
| Explicit `interval` interpretation in source details | 1,028 |
| Those intervals with an effective end | 1,022 |
| Explicit `list_or_phases` interpretation | 183 |

All non-null scalar dates parse as ISO dates. This does not establish that
their dates or field roles match the source. Of the 57,023 paired rows, 9,885
have an effective start before the notice date; 6,789 of those have a 2020
notice date. These should be stratified by source and event, not automatically
discarded or corrected.

## Confirmed and high-confidence problems

### Florida archived ranges are dropped

The candidate has **1,252 Florida archived rows** with `effective_date` and
`effective_date_end` both null, although the preserved `LAYOFF DATE` field has
exactly two valid slash dates joined by `thru`. **1,241** pairs are in ascending
order; **11** are reversed in the source and need review. For example, notice
`58122` (Combustion Tec) has `9/10/1999 thru 10/29/1999` in raw text and no
canonical effective dates. `warnlive/backfill/state_archives.py` calls
`_fl_date(cells[2])`, which accepts only a whole-cell single date. These rows
also bypass the range projection used for current normalized Florida data.
The candidate does preserve 470 other Florida intervals from a different
input path, so this gap is source-path specific.

### New Jersey slash-date months become January

Among **112** New Jersey rows with a simple, fully parseable slash-formatted
source `Effective Date`, **107** have a different saved date: the year and day
match, but the saved month is January. Example: source `4/15/05` becomes
`2005-01-15` for Cingular Wireless (notice `38477`). The affected rows are
from 2005–2007 and 2010–2011. The upstream transformer uses `%M` (minute)
where `%m` (month) is required; the local New Jersey subclass appends a new
format but retains the upstream formats. This is a deterministic parser bug,
not a source ambiguity.

Separately, **2,267 New Jersey rows** have `notice_date_precision='month'`
and a year inferred from the source posting month and effective date. **2,266**
have both scalar dates, but their `notice_date` is not an observed day. They
must not enter an exact-day notice-to-layoff calculation as if the first of
the month were reported. Four New Jersey phase-list rows have a scalar
effective start later than an earlier parsed component; one has no scalar
start despite dated components. Some components may themselves contain source
typos, so those five need row review rather than an automatic minimum-date
replacement.

### Pennsylvania labeled ranges are not captured as ranges

**54 Pennsylvania rows** have two valid, ascending source dates explicitly
labeled `Beginning`/`Commencing` and `Ending`. Their scalar start is present,
but `effective_date_end` is null and the detail says `list_or_phases`. For
example, ABA2DAY (`42407`) says `Beginning 8/21/23 - Ending 9/19/23`.
The range recognizer in `warnlive/normalize/details.py` accepts only a bare
two-date range or the narrower `Beginning: date - Ending: date` form.

There are also two confirmed scalar start errors in Pennsylvania's upstream
date-correction table. Bowhead (`42415`) says `Beginning 9/29/23; Ending
11/16/23` but has `effective_date=2023-09-23`. Cargill Cocoa (`42535`) says
`5/26/25-5/30/25`; the candidate has start `2025-01-31` and end
`2025-05-30`. The latter is the only start mismatch among the candidate's
1,027 intervals with component dates. Six intervals lack an end: five have
reversed source dates, and one has a malformed start date. The projector
retains their source components rather than inventing an end.

### Colorado stores five impossible end-before-start pairs

Five Colorado rows have `effective_date_end < effective_date`, including JVS
Masonry (`15746`: May 31 to May 21, 2025) and Autism Home Support Services
(`15895`: August 21, 2023 to September 19, 2021). The raw `begin_date` and
`end_date` contain these same values. The candidate copies the end without an
ordering check. These are source anomalies or source transcription problems;
the canonical end should be held for review, not treated as a valid range.

### New York historical effective dates remain absent

All **780 New York candidate rows dated 2022–2024** have no effective start;
all 780 came from the historical Big Local News integrated input. None has
`source_details`. New York's cached dashboard CSVs and individual agency
notices provide some of the missing date roles, including the previously
checked [Sodexo notice](https://dol.ny.gov/system/files/documents/2022/06/warn-sodexo-suny-polytechnic-mohawk-valley-2021-0088-6-30-2022.pdf).
This is a coverage and reconciliation problem, not a reason to fill all rows
by employer/date similarity. Older New York archive rows also preserve layoff
date prose that is not projected into canonical dates.

## Other coverage limits

- Arizona, Delaware, Hawaii, Kansas, Maine, Nebraska, Oklahoma, South
  Dakota, Utah, and Vermont have no effective starts in this candidate.
  This is a source-coverage limitation until checked against each agency's
  original fields, not proof that all those parsers are wrong.
- Georgia and Pennsylvania have no canonical notice dates. Michigan has
  notice dates on some rows and effective dates on others, but no row with
  both. None of these three states currently contributes to an exact
  notice-to-effective-date calculation.
- Georgia retains 44 rows with two or more distinct separation dates in
  `source_details`. These are phases, not automatically continuous ranges.
  South Carolina has 33 effective ends and preserves older projected-only
  dates without inventing a notice date. Louisiana's reviewed official
  Cornerstone row has a start and end; the two previously identified reversed
  BLN dates for Denka and McGlinchey are corrected in this candidate.
- Washington's 1,459 rows with preserved `Received Date` and `Layoff Start
  Date` no longer exhibit the old both-fields-equal mapping error when those
  source dates differ.

## Recommended order of work

1. Fix Florida archive range parsing and preserve both dates, quarantining
   the 11 reversed source ranges. Freeze a source-level regression sample.
2. Correct New Jersey's `%M` format bug, rebuild, and verify the 107 source
   slash dates. Mark all month-precision notice dates as ineligible for exact
   daily timing unless an actual notice day is independently sourced.
3. Expand Pennsylvania's labeled-range recognizer, correct Bowhead and
   Cargill against their preserved source rows, and check start/component
   agreement in a rebuild gate.
4. Validate interval ordering before populating Colorado end dates and
   preserve rejected source text with a terminal exclusion reason.
5. Reconcile New York historic filings against preserved official dashboard
   or document evidence, including amendment and site identity. Freeze those
   inputs in the source bundle before promotion.
6. For timing research, report the denominator by state and source, exclude
   unknown/day-imprecise notice dates from exact-day estimates, and review
   negative intervals rather than silently clamping them to zero.

No canonical database rows or exports were changed by this audit.
