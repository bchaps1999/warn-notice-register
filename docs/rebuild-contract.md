# Deterministic WARN assembly contract

The dataset is assembled from preserved agency WARN source rows and documented parser rules. The supported build admits agency-source evidence under explicit source, identity, and date rules. It may use open-source scraper and parser code to collect agency data. The build makes no LLM API calls, does not replay model decisions, and creates no manual review queues. Historical model experiments and their outputs are retained only under `data/archive/retired-adjudication/` and `docs/archive/`.

## Admission

1. Preserve the source bytes, capture URL and date, artifact hash, row pointer, and raw fields. A source-only rebuild writes an exception manifest for every row it cannot admit and `source_observations.csv` for verified agency observations.
2. Admit a notice when its source adapter produces a canonical row that passes parser and source-specific identity checks. An explicit agency filing ID is preferred. A hash or similarity of employer/date/workers is not a filing ID.
3. Exact duplicate observations may be coalesced. The current GA, IA, KS, and NJ adapters exclude ambiguous same-key groups with stable reason codes; source-only rebuilds record these in the exception manifest. Other source-specific rules may exclude rows that cannot be interpreted. No queue or later human action is assumed. Ambiguous phase and multi-site interpretation outside these checks remains a known limit to evaluate before promoting a source-only candidate.
4. Missing optional facts do not exclude an otherwise identifiable notice. Preserve the employer and location as filed; leave unsupported dates, worker counts, geography, industry, and historical parent relationships null or explicitly unknown. A date-shaped string does not prove its legal role or precision.
5. A source-specific, hash-guarded rule may add a field or relationship only when the source states the needed fact. Name similarity, date proximity, or an amendment marker alone cannot establish a link between two events.

## Outputs and coverage

The database, national/state CSVs, site data, source-observation export, and exception manifest must agree. Report admitted and excluded source rows by state, source, year where known, and reason. The published totals describe **admitted notices**, not all WARN filings in the country; known historical and source-format gaps must remain visible. An excluded observation is a final conservative disposition for the current evidence, not an item awaiting review.

## Rebuild and release checks

- Pin dependency and reference versions, source bytes, code revision, and an explicit `--as-of` date for site output. Rebuild from a fresh checkout without network access after inputs are frozen.
- Check that every raw source row is represented as an admitted notice, a linked exact duplicate/version, or an explicit exclusion. Reject format drift, same-key disagreement, and source-hash mismatch rather than silently changing totals.
- Run SQLite integrity and foreign-key checks; compare stable notice/version/link fingerprints, exception checksum, CSV tree, site tree, and state/source coverage between independent runs.
- Compare a staged candidate with published data to expose lost coverage or changed interpretations. Matching old counts is diagnostic, not an acceptance criterion. Promote only a reproducible candidate whose exclusions and material deltas are reported; preserve the prior published artifacts for rollback.

The [prior working contract](archive/rebuild-contract-before-deterministic-policy-2026-09-23.md) records the earlier rebuild investigations and model pilot boundary. Its review and model instructions are historical.
