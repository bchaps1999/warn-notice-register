# Agency-source coverage expansion

This checkpoint extends the [agency-only v4 candidate](agency-only-rebuild-2026-09-23.md)
from preserved state publications. It does not alter the checked-in public
database, exports, or site. The input is
`data/source_snapshots/2026-09-23-agency-only-or-tn-v2.tar.gz` (SHA-256
`8cee536f389a2db3b267be9ee8fb91b9b44b9eb20bdfb1aee13000ff3cda9170`),
derived without BLN data or old-database admission keys.

The isolated replay produced **60,945 notices, 68,716 versions, and
6,302,280 affected workers**. Against the v4 agency-only baseline, that is
**621 additional notices and 67,062 additional workers**. Source accounting
admits 153 of 678 Oregon rows, 139 of 143 Tennessee rows, and 329 of 758 New
York rows; every remaining row is recorded as held. The rebuilt SQLite
database reports integrity `ok` and zero foreign-key errors. The full test
suite passes (329 tests). Two isolated replays match on notice, version, and
link content fingerprints, source accounting, and all totals. Their exception
reports differ only in the output path assigned to each run. This is a staged
candidate, not the public release.

| State | Original-source evidence | Admitted source records | Held source rows | Date-role rule |
| --- | --- | ---: | ---: | --- |
| OR | July agency workbook separated from the old BLN historical workbook, plus a direct September HECC export | 153 | 525 across both captures | `Received Date` is agency receipt; legal notice date stays unknown. `Layoff Date` supplies an exact reported action day when present. |
| TN | Direct state WARN archive HTML, 2021–24 paragraphs | 139 | 4 | `Date Notice Posted` is agency posting; legal notice date stays unknown. Only simple whole-cell action dates become effective dates. |
| NY | Three previously pinned 2022–24 DOL dashboard CSVs | 329 | 429 | The dashboard's named notice, posting, and layoff-start columns remain distinct. |

Oregon's repeated WARN numbers can represent sites or phases. Only one-row
WARN numbers with complete employer, action date, worker count, and receipt
date enter the candidate. The later workbook's rows for existing WARN numbers
are accounted for as overlapping capture evidence; one changed multi-row
group remains held. Six newly published, single-row WARN numbers are admitted.
The July cache's acquisition time is unverified; the September workbook is a
fresh agency download. The rolling agency export does not itself recover all
history removed with BLN.
Of the 525 held Oregon source rows, 307 are repeat capture rows for WARN
numbers already seen in the July workbook, 214 belong to multi-site or phase
groups, and four are incomplete.

Tennessee archive entries are keyed by archive section and the published
notice number. The agency reuses at least one number for different employers;
one archived number also appears in the current raw table. That archived row
is held. An entry with an ambiguous worker allocation, one suffix-number
entry, and one malformed number remain held. The 2025–26 current raw rows
remain the existing pinned agency-only projection, so this work adds only
earlier archive years.

New York's dashboard `Index` repeats and is not a filing ID. Admission uses a
source-observation identity only when a normalized employer appears once in
the three frozen annual exports and no other row shares its site, action, and
posting dates. Remaining rows are held pending filing or amendment evidence.
For example, the two Teacher Synergy display names share site, action, and
posting facts but disagree on notice date and workers; neither is admitted.
The 329 admitted rows are agency source records, not a verified count of
distinct legal filings. A later source revision needs explicit identity
correspondence before replacing or adding those records.
Of the 429 held New York rows, 426 involve repeated normalized employers, one
shares site/action/posting facts with another row, and two are incomplete.

The three projectors require exact artifact bytes, headers, and row counts,
and they account for every source row as admitted or held. The offline rebuild
requires each admitted record to create a unique notice. The next high-yield
work is original filing or archive acquisition for New York's held rows,
direct Georgia historical entries, Oregon multi-site groups, and pre-2020
Texas workbooks or agency records. Counts here are source-supported candidate
coverage, not an estimate of all U.S. WARN filings.
