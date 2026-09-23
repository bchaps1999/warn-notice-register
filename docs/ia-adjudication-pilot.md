# Proposed Iowa adjudication pilot — not yet authorized or run

Purpose: test whether a model can help **draft**, not apply, source-backed
filing/phase decisions for at most 20 Iowa employer/city histories from the
frozen official logs. The 935 official observations remain held until reviewed
decisions cover every row. This pilot must not change the canonical database.

## Inputs and controls

- Input source: `data/source_snapshots/2026-09-23-ia-la-official.tar.gz`, the
  checksummed Iowa correspondence report, and its deterministic review worklist.
- Each model request contains one employer/city history, source artifact hashes,
  original source-row IDs and hashes, raw printed dates, typed dates where
  available, notice type, workers, city, address, and any candidate predecessor
  rows. It does not contain the prior published database's answer as authority.
- No more than 20 histories, 12,000 UTF-8 bytes of rendered input per history,
  and three billed JSON attempts per history. Oversize histories are held for
  human review, not truncated. Use `deepseek-v4-pro`, temperature zero, JSON
  output, and an initial 2,000-token output cap. The existing client may raise
  that cap to 4,000 then 8,000 on truncation.
- Price conservatively at the provider's peak rates as of September 22, 2026:
  $1.32 per million uncached input tokens and $3.96 per million output tokens
  ([official pricing](https://api-docs.deepseek.com/quick_start/pricing/)).
  The worst-case budget for the stated byte/output caps and three attempts is
  below $3. Recheck model availability and pricing immediately before any run;
  stop and ask again if the bound no longer holds. Do not rely only on the
  client's after-the-fact spend meter.

## Exact proposed system prompt, version `ia-history-draft-v1`

> You are reviewing Iowa WARN source observations, not deciding whether an
> employer closed or how many people actually lost work. Return one JSON object
> with a `decisions` array, one item for every supplied source row. Each item
> must contain `source_row`, `disposition`, `predecessor_row` (a supplied source
> row ID or null), `evidence_rows` (supplied source row IDs), and `reason`.
> `disposition` must be one of `duplicate_observation`, `revision`,
> `additional_phase_or_workers`, `new_filing`, or `abstain`. Treat the source's
> employer, location, notice date, effective date, worker count, and notice
> type as separate evidence fields. Never silently repair an invalid date,
> assume an address is a worksite, allocate a multi-site worker total, or
> treat rows with different effective dates or cities as the same phase.
> Employer-name similarity or chronological proximity alone never establishes
> a predecessor. A revision or duplicate must name the specific earlier row
> and cite field-level evidence; an additional phase must remain separate from
> the predecessor's active worker total. If the evidence does not establish
> the relation, choose `abstain`. Do not use facts outside the supplied rows.

The variable user message is a JSON object with `source_bundle_sha256`,
`correspondence_report_sha256`, `history_key`, and `rows`. A row carries the
fields listed above plus `candidate_predecessor_rows`, never an inferred filing
ID. The model output is a proposal ledger keyed by the exact rendered-input
hash, model slug, prompt version, response bytes, and usage; it is not an
admission manifest.

## Evaluation and acceptance

Before paid calls, pin source-backed controls covering: the identical
workbook rows 477/478; the three Wild Rose cities; CNH's 14 distinct effective
dates; Sorenson's malformed historical notice date; a non-Iowa source address;
and Ryder's singleton “Additional Employees” row. A response fails if it
double-counts a literal duplicate, merges distinct cities/phases, repairs an
invalid date without explicit evidence, treats an address as a verified
worksite, invents a predecessor, or omits a source row. Require **zero such
violations** on these controls. Then review every pilot proposal against its
original rows; no model answer enters the database automatically. If the pilot
fails, stop rather than scaling calls. If it passes, agree on the remaining
call scope, budget, and human adjudication procedure before any larger run.

Initial control pointers, to be checked against the source-row hashes in the
correspondence report before use:

| Control | Source rows | Required invariant |
| --- | --- | --- |
| Literal duplicate | `event-log.xlsx:WARN Log:r477`, `r478` (identical hash `b3d9439d9914870a8343103599e51ef0c9bb48247382766e71e550c0d706be9a`) | Do not count 70 twice. |
| Distinct cities | `historical-2023.pdf:p3:r31`, `r32`, `r33` | Clinton, Jefferson, and Emmetsburg stay separate. |
| Invalid printed date | `historical-2023.pdf:p3:r57` (hash `0c7af0e63fe8d21df025ffbadc8242feefce69150982656a087f14659556a32e`) | Do not invent its notice date. |
| Unidentified increment base | `event-log.xlsx:WARN Log:r522` (hash `ef18272150c634aa6243d8c5f5a01d793b03a111e7f74e6ceb2afa9ed323a412`) | Do not invent a predecessor for Ryder's 153 workers. |
