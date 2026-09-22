# Source-first rebuild contract

Status: work in progress, 2026-09-22. The objective is the best defensible WARN dataset we can build from preserved source evidence, with a rebuild another person can verify. Matching the previously published database is **not** an acceptance criterion. It is a diagnostic comparison that can reveal missing sources or regressions, but it can also preserve old parsing, identity, and classification errors.

## Layers of reproducibility

1. **Inputs.** Preserve exact bytes of state captures, historical backfills, and agency documents in a checksum-verified bundle. Record capture date and source URL for each input. A mixed-time bundle is a valid experiment, but not a contemporaneous snapshot.
2. **Source interpretation.** Every canonical notice must trace to source rows or documents. Keep filed values and their roles (notice date, effective-date start/end, worksite, mailing address, per-site workers) separate from inferred values. Record date precision and the evidence for each inference. Do not fabricate a day, place, or worker allocation.
3. **Event identity and merging.** Prefer source filing/event IDs where trustworthy; model phases, amendments, multi-site filings, and duplicates explicitly. If distinct source rows collide under one key, quarantine them with a reason and source references. Do not silently collapse them or discard them to make old totals match.
4. **Derived decisions.** Pin reference datasets, human decisions, prompt and model versions, rendered inputs, and validation rules. Replaying existing decisions must require no network or model calls. A new model decision is a separate, reviewable experiment; it does not overwrite its evidence or the prior answer.
5. **Outputs.** From a fresh checkout and pinned inputs, generate the database, CSVs, and site data in an isolated directory. Pass `warnlive build-site --as-of YYYY-MM-DD` so site metadata and trailing windows do not depend on the wall clock. Compare stable-content fingerprints (excluding SQLite row IDs), source-to-output coverage, unresolved queues, and state-level worker/date/location summaries across two independent runs.

## Acceptance gates before replacing published data

- The bundle is complete enough for the intended historical coverage; omissions and stale state captures are enumerated rather than masked by the old database.
- All raw rows are accounted for as published notices, linked versions/duplicates, or explicit parse/identity/quality exceptions. Rejected or unresolved rows remain inspectable with their source artifact and reason.
- Date range, precision, location role, multi-site allocation, and worker-count checks pass on state-stratified fixtures and sampled original documents, including SC, GA, IA, KS, NJ, and CA edge cases.
- Deterministic portions replay byte-identically in a fresh environment with pinned dependencies and references. Any model-derived portion is represented by a versioned decision ledger and is independently auditable.
- The candidate DB passes integrity checks; exports and site views agree with it; state-level discrepancies are explained by source evidence. Old-DB count or key differences are reported, but are not by themselves failures.
- Promotion is staged and reversible. Only after the candidate passes review should the published dump, exports, and site be replaced and the branch merged to `main`.

## Current gap and model-call boundary

The committed `2026-09-22-ca-nj-refresh.tar.gz` can be replayed offline, but its `rebuild_policy.json` includes historically accepted keys and archive URLs derived from the old database. Most raw state snapshots predate the CA/NJ refresh. Consequently it proves repeatability of *this candidate*, not yet a fully source-independent build. The old database should be used to find possible missing evidence, not to decide which source rows are valid.

The offline rebuild and export path should not make LLM calls. **Before any new or repeated LLM calls** for employer identity, entity resolution, place decisions, industry, or other classification, agree with the project owner on the exact task, input/evidence contract, prompt and model, evaluation set, acceptance thresholds, maximum call/cost budget, and how decisions will be reviewed and recorded. Running every decision again is acceptable if evaluation justifies it; it is not an automatic step of the rebuild.

## Next implementation sequence

1. Make the overlap/curation policy source-evidenced instead of old-DB-derived, retaining a separate read-only old-DB comparison.
2. Fix source-aware identity and exception reporting for SC, GA, IA, and KS. Complete date-range and location-role coverage with raw-to-output tests.
3. Freeze a contemporaneous capture where possible; declare intentionally historical/missing artifacts in the manifest. Add source-to-output coverage and verify two fresh, isolated database/export/site replays.
4. Review unresolved classification and matching queues and a held-out sample with the owner. Only then decide whether to reuse, revise, or rerun LLM decisions.
5. Review the resulting candidate, stage replacement and deployment, then merge to `main` under the owner's Git identity and remove the temporary branch.
