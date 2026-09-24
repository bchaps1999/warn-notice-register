# Working in warn-live

This repository publishes a source-backed WARN notice register. Read `README.md` for the current release and commands, `docs/rebuild-contract.md` for source replay and reconciliation, and the relevant state or release note before changing data policy. Do not treat an exploratory candidate as a published release.

## Source and data integrity

- Follow the existing path from state fetch and normalization through verification, SQLite ingest, exports, and the site. `warnlive/states.yaml` controls each state's adapter, thresholds, cadence, and active status; change it deliberately and review the resulting coverage.
- Preserve the filed source facts and provenance. Distinguish an unavailable source, failed fetch, held or excluded row, amendment, and admitted notice. Do not infer that a missing notice means no WARN filing or that a missing enrichment value means the underlying fact is absent.
- Admit or link records only with source-supported identity. Treat ambiguous matches conservatively. Preserve source-specific IDs, date roles and precision, raw evidence, and the reason for exclusions; do not merge notices based only on similar names or nearby dates.
- Use dated source captures and manifests for historical or release work. Build and compare candidates in isolated paths. Do not overwrite a frozen bundle, published database, exports, or site data while evaluating a change.
- Before regenerating tracked exports or the site from a local database, follow the README's pull-and-`warnlive unpack-db` procedure and confirm the database is current. A stale local database can silently drop notices collected by CI.

## Implementation and checks

- Keep Python pipeline changes in the relevant `warnlive/` module, checks in `tests/`, site code in `site/`, and operational workflows in `.github/workflows/`. Follow the existing organization rather than creating a new layout for a small change.
- For a source, normalization, admission, dedupe, or export change, test the affected cases and check row accounting, stable identities, amendments, date meaning, exclusions, and state coverage as applicable. Compare resulting counts and fingerprints with the appropriate saved baseline; explain intentional differences.
- For a release or publication change, run the relevant regression and source-replay checks described in `docs/rebuild-contract.md`, and verify the database, CSV exports, site payload, and release notes agree. Never present a candidate replay as the released dataset.
- Run focused Python tests during development; run `.venv/bin/python -m pytest tests/ -q` before completing a broad pipeline or release change. For site changes, run `npm test --prefix site` and `npm run build --prefix site`. Report checks that could not run.

## Delegation

- Decide whether delegation will improve accuracy or reduce work before choosing a role. Handle short, routine edits in the primary task. Use `scout` for a bounded read-only code or source search, `utility` for a separable mechanical task, and `implementer` for a bounded coding phase. Use `architect` when source admission or identity policy, release design, or several systems need a design decision; use `reviewer` for a substantial final diff or publication-critical data result.
- Give each agent a concrete question or deliverable, relevant paths, file ownership for edits, and the evidence needed to check its work. Agents share the checkout: avoid overlapping edits and duplicate investigations. The primary agent integrates the result and resolves findings.
- Skip roles whose phase adds no value. For a publication-critical data change, ask an independent reviewer to check source provenance, row accounting, and at least one key result from underlying records after the candidate is stable. Do not repeat a full review for every exploratory rerun.
