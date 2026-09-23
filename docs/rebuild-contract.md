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

The committed `2026-09-22-ca-nj-refresh.tar.gz` can be replayed offline. Legacy comparison mode uses its `rebuild_policy.json`, which includes historically accepted keys and archive URLs derived from the old database. `--source-only` ignores that policy and writes a checksummed, row-level exception manifest for rejected or unresolved current raw, historical raw, archive, and BLN rows. The manifest is an accounting and review queue, not a determination that every excluded row is a duplicate. Most raw state snapshots predate the CA/NJ refresh. The old database should be used to find possible missing evidence, not to decide which source rows are valid.

The offline rebuild and export path should not make LLM calls. **Before any new or repeated LLM calls** for employer identity, entity resolution, place decisions, industry, or other classification, agree with the project owner on the exact task, input/evidence contract, prompt and model, evaluation set, acceptance thresholds, maximum call/cost budget, and how decisions will be reviewed and recorded. Running every decision again is acceptable if evaluation justifies it; it is not an automatic step of the rebuild.

The latest two source-only replays of the mixed-time bundle match exactly on stable notice, version, and link fingerprints and the exception-manifest checksum. Each produced 86,936 notices, 93,073 versions, 9,631 links, and 9,031,813 workers, with SQLite integrity `ok` and zero foreign-key errors. The 37,953-row exception manifest includes one missing current raw source file (LA), 121 parse failures, 1,524 same-key conflicts, 301 occupied-source-month archive rows, 33 invalid BLN employer rows, 6,330 BLN rows excluded because GA/SC/IA source identity remains unresolved, 15,390 superseded BLN rows, and 14,253 unmatched rows after conservative gap filling. All ingested current raw states, historical raw states, archived states, and 88,626 BLN inputs reconcile to represented, coalesced, or explicit exception rows, with zero unaccounted rows. Reconciliation proves no input was silently lost in this replay; it does **not** establish that every represented notice is correctly interpreted or every excluded row should stay excluded. The generated manifest is at `/private/tmp/warn-source-only-20260922-07.exceptions.jsonl` locally and is not committed.

Louisiana's official 2025 and 2026 WARN PDFs are separately preserved under `data/source_snapshots/la/` with byte hashes, source URLs, and capture metadata. They are not yet part of the replay bundle or normalized into this candidate. The 623 LA notices in the candidate come from preserved BLN data, so they are not a substitute for validating the official documents. The PDFs expose date-range, rescission, multi-site worker-allocation, and address-role cases that need explicit source-faithful handling.

## Next implementation sequence

1. Normalize the preserved Louisiana PDFs with source-row evidence, date ranges, rescission status, site/address roles, and unallocated multi-site totals; then include them in a new frozen bundle.
2. Review and resolve source-only archive/BLN overlap queues using source evidence or explicit curation decisions; keep the legacy policy only for read-only comparison. Finish source-aware identity for SC, GA, and IA; Kansas now keys fresh builds by its verified source record number.
3. Freeze a more contemporaneous capture where possible; declare intentionally historical/missing artifacts in the manifest. Verify two fresh, isolated database/export/site replays and sample source-to-output interpretation, not only row accounting.
4. Review unresolved classification and matching queues and a held-out sample with the owner. Only then decide whether to reuse, revise, or rerun LLM decisions.
5. Review the resulting candidate, stage replacement and deployment, then merge to `main` under the owner's Git identity and remove the temporary branch.
