# New Jersey old-key fanouts: source review checkpoint

The [41-group worklist](nj-old-key-fanout-review-2026-09-23.csv) identifies old
candidate notice IDs that nominate more than one distinct raw New Jersey row.
An old dedupe key based on employer, city, and an inferred posting month is not
a filing identifier. The staged rebuild keeps distinct source rows as distinct
**observations** while their event relationship remains under review.

## Novartis, East Hanover, April 2012

Old notice ID `37753` nominated seven new observations (`37780`–`37786`) with
the same employer, city, posting month, and effective day. The seven workforce
values are 5, 93, 35, 32, 1,380, 45, and 27. The [official 2012 New Jersey WARN
archive, page 1](https://www.nj.gov/labor/assets/PDFs/WARN/2012%20WARN%20Notice%20Archive.pdf)
publishes seven separate table rows with precisely those values under `Company`,
`City`, `Month Posted`, `Effective Date`, and `Workforce Affected`. This confirms
that the fanout is not an accidental multiplication introduced by the current
parser. It does **not** establish whether the seven table rows came from one
multi-part filing, seven filings, amendments, or another agency reporting unit;
the archive supplies no filing control number or legal notice date. Preserve the
seven source observations and their worker values. Do not sum, collapse, or
claim seven independent WARN events without further filing-level evidence.

The same official page includes a March 2012 Novartis row with the same June 8
effective day and five workers. Even exact employer, city, and effective day
would therefore merge rows across posting months if used as an event key.

## Novartis, East Hanover, November 2024

Old notice ID `36668` nominated two source observations: a five-date list with
86 workers and an ordered February 21–August 28, 2025 range with 53 workers.
The [official 2024 New Jersey WARN archive, page 3](https://nj.gov/labor/assets/PDFs/WARN/2024_WARN_Notice_Archive.pdf)
publishes both as separate November table rows. The range's endpoints may be
represented as an effective start and end; the list remains phases, with no
continuous end inferred. The distinct source rows support distinct observation
anchors, but do not reveal whether they belong to the same legal filing or an
amendment. The posting month is not a notice date.

## Next review step

Continue the 41 groups and 23 repeated-row groups against the dated agency
archives. The public archives establish table-row correspondence, not always
filing identity. Resolve event links only when filing-level evidence or a
source-provided control number supports them; otherwise leave the relationship
unknown and keep the observation-level counts labeled as such. This checkpoint
makes no change to the staged canonical notices or strict timing cohort.
