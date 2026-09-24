# Washington agency receipt date correction

The [Washington Employment Security Department (ESD) WARN database](https://esd.wa.gov/employer-requirements/layoffs-and-employee-notifications/worker-adjustment-and-retraining-notification-warn-layoff-and-closure-database)
explicitly defines `Received Date` as the date **ESD receives** the WARN
notice. It separately describes `Layoff Start Date` as the date a layoff or
closure takes effect and offers the underlying notice for download. Agency
receipt is not proof of the employer's dated notice to employees or the legal
notice date inside the filing. The [ESD requirements page](https://esd.wa.gov/employer-requirements/layoffs-and-employee-notifications/warn-requirements)
also separates an employer's notice to workers from its submission to ESD.

The installed upstream transformer had mapped `Layoff Start Date` to both
canonical notice and effective date. The project's earlier Washington override
changed canonical `notice_date` to `Received Date`. That removed a clear
same-day error but still labeled agency receipt as legal notice in the
database and provisional timing cohort. The frozen `raw/wa.csv` contains 1,482
rows, each with both a received date and a layoff start; it has no nonempty
`Notice` URL cells. The v5 candidate has 1,459 Washington notices because 23
raw observations share a legacy dedupe key with another observation. None
has a source-supported legal notice day merely from the raw table.

The normalization rule `wa_agency_received_role_v1` now leaves canonical
`notice_date` null and preserves the source's raw `Received Date`, parsed day,
source field, and `agency_received` role in `source_details`. It retains the
existing internal key based on employer, place, and agency receipt day to
avoid collapsing more observations during this date-role correction. A
read-only normalization of the frozen WA source produced 1,482 records with
zero parse failures, 1,459 distinct keys, 1,459 exact source-ID/key matches
to the v5 candidate, and zero nonempty canonical notice dates. The 23
same-key observations remain excluded as unresolved event/version identity; matching an old
key does not prove they are one filing.

The correction changes date meaning, not a source-reported effective range.
No legal notice date is inferred from `Received Date`, and the strict timing
gate already excluded these rows because it requires explicit source-supported
notice precision and basis. The guarded v6 replay removes Washington's 1,459
prior provisional formatted pairs. Filing-level notice
dates can be added later only through reviewed document correspondence.
An initial combined diagnostic replay exposed a downstream effect: the BLN
older-row selector saw the cleared notice date and admitted 1,335 additional
Washington rows without a source filing correspondence. The source-only
rebuild now excludes Washington from that conservative BLN admission path and
records those BLN rows as source-identity unresolved. This preserves the
observation-level boundary until their documents or filing identifiers can be
reviewed. The inflated diagnostic build is not a candidate checkpoint. Two
final source-only replays with the guard match at 1,459 Washington notices;
the [v6 state summary](date-eligibility-state-summary-2026-09-23-v6.md)
records the date-eligibility effects.

The fixed-code diagnostic replay restores the 1,459 Washington notice count
and prior worker total. It removes 247 old Washington `notice_links` because
the link detector required the falsely labeled notice day. Those prior link
claims are preserved in the [WA/MN link review sheet](wa-mn-legacy-link-review-2026-09-23.csv)
for filing-level reassessment; no link is recreated from agency receipt alone.
