# Reproducible candidate versus current local database: v10

This is a read-only release diagnostic. The current local `data/warn.sqlite` has SHA-256 `55e04c926f698580c17357db824d8153c463117769add6c012f878d8f10f64d7`. The isolated v10 source-only candidate is `/private/tmp/warn-role-ri-v10-a-20260923.sqlite`, built from the pinned Texas annual-workbook bundle at `--observed-at 2026-09-22`. It has an independent matching replay. The [state table](staged-candidate-vs-current-db-2026-09-23-v10.csv) gives both populations and differences for every state. This comparison does not replace the local database or the published CSV/site files.

| Measure | Current local DB | V10 candidate | Difference |
| --- | ---: | ---: | ---: |
| Canonical notices | 92,271 | 85,954 | -6,317 |
| Reported affected workers | 9,701,472 | 8,898,111 | -803,361 |
| Non-null legal notice dates | 75,391 | 63,464 | -11,927 |
| Non-null effective starts | 77,015 | 74,385 | -2,630 |
| Non-null effective ends | 0 | 2,652 | +2,652 |

The largest notice-count changes are Washington **-1,409**, Kentucky **-954**, Illinois **-847**, Kansas **-604**, Iowa **-590**, New York **-472**, Michigan **-468**, and Georgia **+452**. The Kentucky difference includes the v9 conservative exclusion of 481 BLN-only historical keys whose filing identity and notice role are unverified. Washington's current-source `Received Date` was reclassified as agency receipt; its BLN-only additions are held under the same source-identity rule. The v10 exception manifest records 40,069 source rows, including 12,335 `source_identity_unresolved`, 9,270 `unmatched_after_conservative_fill`, and 15,390 `superseded_source_row`. The [admitted coverage](admitted-coverage-by-state-source-year-v10.csv) and [exception summary](source-exceptions-by-state-source-year-v10.csv) give state, source, and year denominators. These raw-row counts do **not** map one-for-one to lost canonical notices: one notice can have multiple source observations, and some excluded rows duplicate admitted events.

The candidate's 2,652 effective ends include 1,241 Florida archived continuous ranges, reported ends from other sources, and five Rhode Island ranges added in v10. A stored range end is not automatically eligible for research on days from legal notice to separation. The strict source-supported cohorts have only **4,815** start pairs and **1,251** end pairs; 52,404 formatted start pairs remain provisional because date role or precision is unassessed. Rhode Island's five new ends do not enter strict timing without a source-supported notice day.

Before replacing current artifacts, the material state changes need a source-by-source coverage explanation, especially the Washington, Kentucky, Illinois, Kansas, and Iowa losses. The [rebuild progress log](rebuild-progress.md) records the source-role corrections and replay checks. A clean frozen-code checkout and final candidate-versus-published CSV/site comparison remain open release gates. The current local database is an existing comparison point, not a known complete ground truth; matching its counts is not the acceptance rule.
