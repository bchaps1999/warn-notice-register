# Working in warn-live

This repository publishes a source-backed WARN notice register. Read `README.md` for the current release and commands, `docs/rebuild-contract.md` for source replay and reconciliation, and the relevant state or release note before changing data policy. Do not treat an exploratory candidate as a published release.

## Branches and CI

- `main` is the working branch. Scheduled scrapes commit the database dump and exports to `main` daily, so keep any feature branch short-lived and merge it promptly; a long-lived branch diverges from the bot's data commits and cannot be rebased over the regenerated dump.
- A green `Tests` run does not prove that the scheduled scrape path works. After merging a change that touches `warnlive/cli.py`, `warnlive/pipeline.py`, ingest, dedupe, or `states.yaml`, run the scrape path locally against a copy of the database (for example `warnlive scrape --cadence daily --run-report <tmp>/run.json` in an isolated worktree) or trigger `scrape-adhoc.yml`, and check the next scheduled run. Before ending a session that merged such a change, check recent Actions results (`gh run list`, or the public API `https://api.github.com/repos/bchaps1999/warn-notice-register/actions/runs`).
- A failing scheduled scrape means the published register stops updating. Treat it as the highest-priority defect.

## Source and data integrity

- Follow the existing path from state fetch and normalization through verification, SQLite ingest, exports, and the site. `warnlive/states.yaml` controls each state's adapter, thresholds, cadence, expected columns, and active status; change it deliberately and review the resulting coverage.
- Preserve the filed source facts and provenance. Distinguish an unavailable source, failed fetch, held or excluded row, amendment, and admitted notice. Do not infer that a missing notice means no WARN filing or that a missing enrichment value means the underlying fact is absent.
- Account for every source row: it is admitted, held with a recorded reason, or reported as a parse failure. An unparseable optional field (a worker count, a secondary date) blanks that field and keeps the row; it never silently drops the notice. Never replace a source value with a guessed one: a corrected value needs source evidence, and the original cell stays in `source_details` or the raw payload.
- Admit or link records only with source-supported identity. Treat ambiguous matches conservatively. Preserve source-specific IDs, date roles and precision, raw evidence, and the reason for exclusions; do not merge notices based only on similar names or nearby dates.
- The live scrape path and the offline rebuild path must apply the same admission, hold, and enrichment rules. When adding a rule or enrichment to one, add it to the other and test both, or document why they differ.
- Use dated source captures and manifests for historical or release work. Build and compare candidates in isolated paths. Do not overwrite a frozen bundle, published database, exports, or site data while evaluating a change. Frozen bundles, release manifests, and hashed evidence directories are immutable; corrections go to a new dated path.
- Before regenerating tracked exports or the site from a local database, follow the README's pull-and-`warnlive unpack-db` procedure and confirm the database is current. A stale local database can silently drop notices collected by CI.
- Prefer general parsing rules over per-row patches. Add a state- or row-specific rule only with source evidence for that case, and remove rules that no longer earn their place.

## Implementation and checks

- Keep Python pipeline changes in the relevant `warnlive/` module, checks in `tests/`, site code in `site/`, and operational workflows in `.github/workflows/`. Follow the existing organization rather than creating a new layout for a small change.
- Keep module docstrings current: what the module does, its inputs and outputs, and any network access. Update them when behavior changes.
- Replace, don't accumulate. When code is superseded, delete it in the same commit as its replacement, with its tests and README commands; Git history is the archive. Superseded notes worth keeping go to `docs/archive/` with a line explaining why; do not keep numbered drafts (`-v3`, `-v4`, ...) side by side in `docs/`.
- For a source, normalization, admission, dedupe, or export change, test the affected cases and check row accounting, stable identities, amendments, date meaning, exclusions, and state coverage as applicable. Compare resulting counts and fingerprints with the appropriate saved baseline; explain intentional differences.
- For a release or publication change, run the relevant regression and source-replay checks described in `docs/rebuild-contract.md`, and verify the database, CSV exports, site payload, and release notes agree. Never present a candidate replay as the released dataset.
- Record what actually ran for any candidate or release build in its dated note under `docs/`: exact command, code commit (commit first if the tree is dirty), bundle path and SHA-256, counts admitted and held by reason, checks and their outcomes, and unresolved problems. Record failed and abandoned attempts too. Do not silently rewrite an earlier note; add a correction.
- Run focused Python tests during development; run `.venv/bin/python -m pytest tests/ -q` before completing a broad pipeline or release change. For site changes, run `npm test --prefix site` and `npm run build --prefix site`. Report checks that could not run. Do not call a result verified if a required check failed or never ran.
- Keep `README.md` describing the current release, layout, and runnable commands, not a history. Update it in the same commit as the change that makes it stale.

## Machine resources

Work runs on the user's laptop (16 GB RAM, limited disk). Run at most one full offline rebuild or full-state scrape at a time across all agents, log long jobs to a file, and put scratch databases and worktrees in the session scratchpad or `/tmp`, removing them when done.

## Delegation

- Decide whether delegation will improve accuracy or reduce work before choosing a role. Handle short, routine edits in the primary task. Use `scout` for a bounded read-only code or source search, `utility` for a separable mechanical task or a pre-specified run, and `implementer` for a bounded coding phase that needs judgment. Use `architect` when source admission or identity policy, release design, or several systems need a design decision; use `reviewer` for a substantial final diff or publication-critical data result.
- Give each agent a concrete question or deliverable, relevant paths, file ownership for edits, and the evidence needed to check its work. Agents share the checkout: avoid overlapping edits and duplicate investigations. When delegating in parallel, name which agent, if any, may run a heavy rebuild or full test suite.
- Subagents report what ran (commands, paths, counts, test results) and leave Git to the primary agent, which integrates the result, resolves findings, and commits.
- Skip roles whose phase adds no value. For a publication-critical data change, ask an independent reviewer to check source provenance, row accounting, and at least one key result from underlying records after the candidate is stable. Do not repeat a full review for every exploratory rerun.

## Git

- Commit after a coherent, tested milestone. Stage named paths and inspect the staged diff.
- Commit as the user's configured identity (`bchaps1999`); check `user.name`, `user.email`, and any `GIT_AUTHOR_*`/`GIT_COMMITTER_*` overrides first. Do not add `Co-Authored-By` or other tool attribution trailers.
- Do not force-push, delete remote branches, create release tags, or change repository secrets without the user's explicit authorization.

## Keeping these instructions current

When a change in workflow, layout, or the user's standing guidance makes a statement here out of date, update this file in the same commit, record dated user guidance here, and tell the user it was recorded.
