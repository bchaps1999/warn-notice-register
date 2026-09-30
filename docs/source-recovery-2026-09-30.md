# Source-backed recovery of lost history (started 2026-09-30)

v1.0 dropped the Big Local News mirror rows under the agency-only policy, and
v1.2.0 (80,703 notices) holds about 11,500 fewer notices than the 2026-09-14
export. This note records the work to restore that history from official
agency sources only, state by state.

## Decisions

- **2026-09-30. Hawaii paused.** `states.yaml` sets Hawaii to `archive` with no
  cadence. The upstream collector's current output re-keys every released
  Hawaii row, so the weekly sweep would have added about 390 duplicates. The
  published Hawaii notices stay in the register. Scheduled collection resumes
  once a key migration maps the new keys onto the released ones.
