# Source-backed date precision expansion

The 2026-09-24 source-only release was replayed with automatic day-precision rules. A rule is limited to a named state/source field with an established date role. It parses the entire raw cell and requires that day to equal the canonical value. California historical layoff dates also require the archived PDF table's expected column order; Wisconsin historical dislocation dates use the workbook's own Excel date mode. The original source cells remain in notice versions, and each accepted scalar day has a rule and field pointer in `source_details`.

| Canonical date | Present | Explicit day before | Explicit day after | Still unassessed |
| --- | ---: | ---: | ---: | ---: |
| Legal notice | 46,436 | 13,095 | 34,804 | 11,632 |
| Effective start | 65,813 | 17,922 | 54,641 | 11,172 |
| Effective end | 2,352 | 1,312 | 1,597 | 755 |

The isolated replay retained exactly **71,490 notices, 79,261 versions, 7,482,177 reported workers, and zero links**. Notice identities, scalar date values, employer, location, and workers are unchanged. The regenerated national CSV differs only in the six precision/basis fields and `source_details`; source observations and health output are byte-identical.

The replay's SHA-256 content fingerprints are `1773efba4152e34e8a21cfc5ddb0c08d4c1c7dde77aa4167ae3437bcb2cf700d` for notices and `f4d14fd37b139ef801ccdd8b1bdffa3a863d4da21ad0462861b85394f60c53c2` for versions. They supersede the earlier v1.0.1 replay report's fingerprints because the metadata is now versioned.

With the expanded evidence, the strict timing gate admits 34,039 notice-to-start pairs and 1,529 notice-to-end pairs. It retains 5,878 negative start intervals and 54 negative end intervals as reported-source quality signals rather than silently discarding them. Those counts describe this source-only release, not a national estimate of WARN notice compliance.

Unassessed values remain where a source field is missing, ambiguous, multi-date, mismatched to the selected value, or not in the reviewed role allowlist. A receipt, posting, or agency notification day does not establish the legal notice day. The remaining null counts are a source-evidence backlog, not evidence that the dates are wrong.
