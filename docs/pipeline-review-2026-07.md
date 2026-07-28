# Pipeline review — July 2026

> **Status (2026-07-27): fixes applied.** Everything in the §7 priority
> list is implemented, plus most of the HIGH/MEDIUM items behind it; the
> full test suite passes (132 tests, 22 of them new/updated). Notable
> consequences and deliberate deferrals:
>
> - **Identity prompts re-ask.** The stance-router removal is prompt
>   version `identity-v4`; ledger answers under v3 do not replay, so the
>   next `adjudicate identity` run re-buys the queue under the new
>   contract. Model rejections now stage (gate `model-rejected`) instead
>   of writing reject overrides; existing reject rows in
>   `identity_overrides.csv` were left untouched.
> - **Acceptance is stricter.** Identity now requires a CIK-anchored
>   corroborator (Wikidata-by-CIK or Exhibit 21) among the witnesses; the
>   name-in-state pseudo-corroborator is gone; `adjudicate confirm` only
>   accepts where ≥1 corroborator already agreed, takes
>   `--proposer-model/--proposer-provider` (fixing a latent bug where a
>   different-model confirm found an empty queue), and warns when the
>   confirming model is the proposing one.
> - **The committed DB changed format**: `data/warn.sql.gz` (rsyncable
>   gzipped SQL dump) replaces `data/warn.sqlite.gz`; `warnlive
>   unpack-db` restores either. Workflows now fail on push conflicts
>   instead of rebasing binaries.
> - **Deferred by design:** A1's key change (folding `effective_date`
>   into null-date dedupe keys) — it re-keys ~4,400 existing GA/PA rows
>   and needs a migration; suspected collisions are now counted and
>   logged at ingest instead (`IngestStats.suspected_collisions`).
>   Also deferred: registrant-geography corroborator (needs a reference
>   refresh to carry state-of-business), GLEIF pagination-total gate,
>   Exhibit 21 era-awareness, `site/dist` untracking (a `git rm` the
>   repo owner should do deliberately), and the low-severity items not
>   listed in §7.

A full review of the warn-live pipeline: scraping/fetch, normalization/aggregation,
storage/dedupe, non-LLM enrichment, the LLM adjudication layer, site export, and
CI/orchestration. No changes were made; every item below is a finding with a
suggested fix. Findings are referenced by file:line as of commit f31b7b2.

**Reading order:** Section 1 is the LLM enhancement layer (the review's focus,
including company matching). Sections 2–5 cover the rest of the pipeline.
Section 6 is a consolidated priority list.

---

## 1. LLM enhancement layer (adjudicate/)

### 1.0 What the design gets right

Worth stating first, because the architecture is genuinely better than most
LLM-enrichment pipelines:

- **Propose a name, never a fact.** Model output is treated as a search query,
  not an answer: proposed registrant names must clear the unmodified EDGAR
  matcher (`identity.py:301-319`), place proposals must resolve in the real
  gazetteer (`places.py:183-202`), parent CIKs are looked up rather than
  trusted (`identity.py:527-543`).
- **The ledger** (`ledger.py`) — keyed by (task, input_key, prompt_version,
  model), append-only, gzip-member-safe under flock, replayed through today's
  gate rather than around it (`queue.py:9-15`). Prompt edits force a version
  bump or the run re-asks; sweep configurations that change the answer are part
  of the key (`sweep.py:58-76`). This is the right shape for affordable,
  auditable LLM batch work.
- **Budget metered before each call**, truncation retried with the ceiling
  raised, malformed answers recorded rather than re-bought (`client.py`,
  `queue.py:240-257`).
- **Calibration for industry** with a tune/test split and
  precision-at-coverage curves (`industry.py:183-226`, `score()`), and the
  Salisbury lesson encoded as policy: county answers are never written on the
  model's word because nothing can check them (`places.py:130-150`).
- **Honest documentation of failures** — the v2→v3 prompt regression note
  (`identity.py:249-254`), the "43 of 70 stalls were invented CIKs" note
  (`identity.py:527-537`).

The concerns below are mostly places where the implementation is weaker than
this philosophy, not where the philosophy is wrong.

### 1.1 HIGH — The stance label is an unverifiable router in front of every gate

`identity.py:80-133` asks the model to classify each employer ("public",
"subsidiary", "private", "government", "franchise", "unknown") *before* any
matching, and the prompt instructs that proposals be "Empty unless stance is
public" (`identity.py:93`). The stance then routes:

- `"public"` → the only path where the matcher + corroborator gates run
  (`identity.py:471`)
- `"subsidiary"` → parent-link path (`identity.py:521`)
- `"private"/"government"/"franchise"/"nonexistent"` at confidence ≥ 0.8 →
  a **permanent rejection override**, on the model's word alone
  (`identity.py:580-593`)

The entire safety architecture — matcher, two corroborators, Exhibit 21
contradiction — engages only *after* an ungated model classification decides it
should. A wrong "private" is caught by nothing; it silently prevents the gates
from running and then writes a rejection that removes the employer from every
future queue. The least-verified branch is the most permanent one.

This has already misfired: the v2 prompt note (`identity.py:249-254`) records
direct identities falling from 25 to 12 in 200 rows when the framing nudged the
model away from "public" — losing Fleming, Sykes, and JP Morgan Chase, where
the obvious name was simply right. The taxonomy is also not a partition:
subsidiaries with registered debt have their own CIKs; "public" in the prompt
really means "has a CIK, possibly decades ago" (the Mervyn's example);
franchise/private overlap.

**Suggested redesign — let the type emerge from what verifies.** Ask one
generative question for every row: "what SEC registrant names might this
employer be found under, and who might own it?" Always collect proposals and a
possible parent. Then:

- a proposed name that clears matcher + corroborators → identity override
- a parent name that clears the matcher (plus an ownership corroborator —
  see 1.4) → parent link
- nothing proposed / nothing clears → unresolved, recorded in the ledger

Keep the stance as *explanatory metadata* for notes and review triage; stop
using it to gate whether proposals are attempted. The "rejections drain the
queue" function does not require permanent overrides: the ledger already stops
re-asking (`ledger.py:120-138`); reserve `decision: reject` rows in
`identity_overrides.csv` for humans, or require agreement across two
models/prompts (see 1.6).

**Cheap experiment before committing:** re-ask a worker-weighted sample of the
"private"/"unknown"-stanced employers under an always-propose prompt (new
`prompt_version`, so nothing blends) and count how many produce matches that
clear the *existing* gates. The ledger makes this a bounded, one-off cost.

### 1.2 HIGH — The confirm stage accepts matches with zero independent corroborators

`confirm.py` exists to rescue matches that cleared the matcher but that no
independent record could corroborate. Its acceptance condition
(`confirm.py:173-187`) is: the same model family says `same: true` at
confidence ≥ 0.8. Three problems:

1. **It inverts the identity design.** `identity` requires *two* independent
   corroborators (`identity.py:78`, `MIN_CORROBORATORS = 2`); `confirm`
   accepts on *zero*, substituting the model's judgment for evidence. The
   override note records "no independent corroborator, confirmed by model"
   (`confirm.py:179-186`), which is honest, but nothing downstream
   distinguishes these weaker identities from corroborated ones.
2. **The evidence shown to the confirm model is not independent.** The
   registrant's filing span, SEC industry, and ticker (`confirm.py:97-119`)
   are exactly the inputs `_corroborate` already checked deterministically and
   found wanting. The model is being asked to re-grade evidence that already
   failed, with a lower bar.
3. **Correlated errors.** Proposer and confirmer are the same model family
   (both default to DeepSeek flash); a systematic misconception about an
   obscure company will survive both stages.

**Suggestions:** (a) run confirm with a *different* model (the providers.yaml
plumbing already supports this — one flag); (b) require confirm *plus* at
least one corroborator, rather than confirm *instead of* corroborators;
(c) carry a `basis` field on identity overrides (corroborated vs
model-confirmed) into `annotate` and the site, so consumers can see the
difference; (d) periodically hand-audit a worker-weighted sample of
model-confirmed identities and track the observed precision.

### 1.3 HIGH — The corroborators are weaker and less independent than "two witnesses" implies

`_corroborate` (`identity.py:362-432`) treats five signals as equal, but they
are not:

- **Filing-span coverage** (`identity.py:381-384`): any long-lived registrant
  covers any notice span. For a namesake registrant that happens to be old,
  this is a near-free witness. Low specificity.
- **2-digit sector agreement** (`identity.py:415-419`): 20 sectors, and WARN
  filers cluster heavily in a handful (manufacturing, retail, admin services).
  Two same-industry namesakes — two staffing firms, two logistics companies
  sharing a name — pass this trivially. The most likely false-positive
  scenario (same-industry namesake) is exactly the one this corroborator
  cannot reject.
- **IRS/GLEIF name-in-state** (`identity.py:422-429`): this checks the
  *employer's name* against those rosters, **not the matched CIK**. It attests
  that *some* entity by this name exists in a filing state — which is equally
  consistent with the match being wrong (the WARN filer is the local
  nonprofit/LLC, not the SEC registrant). A nonprofit namesake in-state
  arguably argues *against* the SEC identity, yet it counts *for* it.
- **Wikidata-knows-CIK-by-this-name** (`identity.py:394-398`) and **parent's
  Exhibit 21** (`identity.py:408-411`) are the two genuinely CIK-anchored,
  independent witnesses.

So "two corroborators" can in practice mean "old registrant + same broad
industry" — the precise signature of a namesake. Combined with 1.2, the
weakest matches then get a second chance through confirm.

**Suggestions:** tier the corroborators — require at least one *CIK-anchored*
witness (Wikidata-by-CIK, Exhibit 21, or a new one below) among the two; drop
or demote the name-in-state check (or make it verify the CIK's own state);
and add the strongest cheap corroborator currently missing: **geography**. The
EDGAR submissions API already provides each registrant's business address
(`sic_refresh` fetches these JSONs, `edgar.py:220-258`); recording
state-of-business in the reference file would let `_corroborate` check
"registrant is headquartered/incorporated in a state it filed WARN notices
from" — a real question namesakes frequently fail.

### 1.4 HIGH — Parent links are accepted with no evidence of ownership

The subsidiary path (`identity.py:521-578`) verifies that the proposed parent
*exists* (matcher hit + presence in a reference file) and that confidence
≥ 0.8 — and nothing else. By construction it runs only when Exhibit 21 does
*not* list the employer (`identity.py:522-526` abstains when it does), so
every written parent link rests entirely on the model's claimed ownership of
a real company. A hallucinated-but-plausible parent ("this staffing firm is
owned by Aramark") passes if Aramark exists — which it always does.

The First Transit conflation the module docstring warns about
(`identity.py:19-21`) is prevented in the *identity* direction but not in the
*ownership* direction.

**Suggestions:** corroborate ownership with at least one independent source
before writing: GLEIF publishes parent relationships (Level 2 data — the
GLEIF integration already exists in `enrich/gleif.py`), Wikidata has
owned-by/parent-organization properties keyed to entities you already index
by CIK. Either would convert this path to the same propose-then-verify shape
as identities. Failing that, stage parent links for human review rather than
writing them, or mark them with a distinct basis downstream.

### 1.5 HIGH — Overrides and rejections are keyed by normalized name alone, globally and forever

`identity_overrides.csv`, `subsidiary_overrides.csv`, and rejection rows are
keyed on `normalized_name` with no state or era scoping
(`identity.py:61-68`, `write()`). Consequences:

- **Namesake collision across the dataset.** One decision claims *every*
  notice with that name, in every state, in every year — past and future. The
  code itself acknowledges "Cardinal Logistics is a hundred companies"
  (`identity.py:178-182`) and shows the model per-employer sites to
  disambiguate, but the *written decision* discards that context: whichever
  namesake dominated the queue row decides for all of them, including
  notices from a different company of the same name that files next year.
- **Rejections never expire.** A "private" company that later IPOs, or a
  wrong rejection (see 1.1), permanently blocks identity — there is no TTL,
  no re-examination trigger, and `write()` explicitly refuses to update
  existing decisions (`identity.py:634-655` — right for conflicts, but it
  means wrong decisions are also permanent absent manual edits).

**Suggestions:** the queue already aggregates per-name across states, so full
(state, era) scoping is a real design change — but two cheap mitigations:
(a) record the states/years the decision was based on in the override row,
and have `annotate` (or a health check) flag notices outside that envelope
rather than silently applying the override; (b) add a periodic re-queue of
model-made rejections above a worker threshold (e.g., re-ask yearly under the
current prompt version — the ledger makes the cost incremental).

### 1.6 MEDIUM — Confidence thresholds are uncalibrated everywhere except industry

The places module proves, with the Salisbury example, that model confidence
does not track truth (`places.py:26-32`: 0.99 on a place that does not
exist) — and industry gets a measured precision-at-coverage curve before its
threshold is chosen (`industry.py`). Yet identity and confirm gate on raw
confidence at a default 0.8 (`cli.py:566`) that nobody has measured, and the
sweep harness (`sweep.py`) exists only for industry. For identity the
corroborators carry most of the weight, so this is partially defensible — but
confirm (1.2) leans on confidence *alone*, and the EMPTY_STANCES rejections
(1.1) are gated *only* by confidence.

**Suggestions:** build a small labeled identity set (the hand-decided rows in
`identity_overrides.csv` + a sample of staged rows, adjudicated once by a
person) and measure identity/confirm precision at the threshold actually in
use; extend the sweep harness beyond industry. Alternatively, replace
confidence gating on the unverifiable paths with agreement between two models
(temperature 0 across two providers is cheap with the existing plumbing) —
disagreement → stage.

### 1.7 MEDIUM — The matcher gate's own soft spots are the LLM layer's safety floor

Every accepted identity passes through `enrich/edgar.py`'s Matcher, so its
false-positive modes bound the whole layer (details in §4):

- weak-word guard covers only single-word names — generic multi-word names
  ("American Industries") exact-match whatever shell registered them
  (`edgar.py:296-309`);
- `_post_era_cik` relaxes forward without bound — a 2026 proposal can match a
  registrant dormant since 1994 (`edgar.py:311-329`);
- bare franchise-brand names ("McDonald's") match the franchisor's CIK with
  no franchise handling anywhere.

Notably, the model *proposing* names makes these more reachable, not less: the
matcher was tuned against filed names, and proposals are cleaner and more
canonical than filed names, so they hit the exact-match paths more often.
When tightening the matcher (§4), re-run the identity dry-run replay
(`--dry-run` re-judges the ledger for free) to see how many accepted rows
survive.

### 1.8 MEDIUM — Provider portability of the `thinking` parameter

`client.py:271` always sends `"thinking": {"type": "enabled"|"disabled"}` —
a DeepSeek-native extension. The OpenRouter provider is documented as "a
second opinion costs a flag" (`providers.yaml:35-38`), but non-DeepSeek
models via OpenRouter may reject or silently ignore this field, and the
cache-hit accounting assumptions (`providers.yaml:12-14`) differ too. Worth a
per-provider capability flag in providers.yaml before the second-opinion
workflow (1.6) is leaned on.

### 1.9 LOW — Operational items

- **Ledger growth**: `adjudications.jsonl.gz` is append-only, fully loaded
  into memory per run, and committed to git. Superseded entries (old prompt
  versions, failed shapes) accumulate forever. Add an occasional compaction
  (keep newest per key, archive the rest) before it becomes the largest file
  in the repo.
- **`Ledger.answered()` / `has_any()`** scan every entry per call
  (`ledger.py:120-152`) — O(queue × ledger). Fine today; index by
  (task, input_key) when the ledger reaches hundreds of thousands of rows.
- **`Matcher.ticker_for`** is a full-reference linear scan per call
  (`edgar.py:388-394`) and is called inside the identity gates; build a
  cik→ticker dict once.
- **Sequential batches**: a 40k-row queue at batch 8 is ~5k serial calls.
  The ledger's per-batch flush makes modest concurrency (4–8 workers) safe
  and would cut wall-clock by that factor.
- **Stale staging files**: `identity.write()` only rewrites
  `identity_adjudicated.csv` when something staged (`identity.py:660-666`);
  a clean run leaves the previous run's staging file in place, which can
  mislead review.

---

## 2. Scraping / fetch layer

Overall: sound contract — per-state isolation, `min_rows` gates before
ingest, health history, GitHub issues on failure, pinned upstream SHA. The
systemic weaknesses: cache-dependent behavior that CI's cacheless runners
violate, one silent-fallback hazard, and warn-level ("degraded") states never
escalating to a human.

**HIGH**

- **S1 — Dispatcher silently falls through past a patch to a known-broken
  upstream scraper.** `fetch/__init__.py:20-25` catches `ModuleNotFoundError`
  regardless of *which* module failed to import. If `patches/tx.py` fails
  because `bs4`/`openpyxl` is missing, the patch is skipped and the WAF-blocked
  upstream scraper runs instead — misdiagnosed as an upstream outage. Fix:
  re-raise unless `e.name` is the state module itself.
- **S2 — CI has no persistent cache**, so Georgia's "coverage accumulates
  across runs" premise (`patches/ga.py:9-12`) is false where scheduled runs
  actually happen; GA re-downloads every detail page each run (thousands ×
  ~1.5 s sleep, risking the weekly timeout), and NC/MN re-download immutable
  archive PDFs every run. Fix: `actions/cache` for `workdir/cache`, or commit
  immutable artifacts somewhere durable.
- **S3 — NM patch drops the first row of every page in non-first PDFs.**
  `patches/nm.py:56-59`: `row_index` resets per page, so row 0 of *every*
  page is skipped when `pdf_index > 0` — real data rows lost on continuation
  pages. Fix: detect headers by content, not position.

**MEDIUM**

- **S4 — Degraded states never escalate.** Issue-opening requires
  `consecutive_failures >= 1` (`cli.py:1021-1025`); freshness/schema-drift are
  warn-only, so a portal that quietly stops updating sits at "degraded"
  forever with no alert. CO and GA are in permanent schema-drift yellow right
  now — which trains you to ignore yellow. Fix: track a degraded streak and
  open an issue after N runs, or fail freshness at 2× `staleness_days`.
- **S5 — Missing `raise_for_status` / explicit encoding**: OH current +
  historical fetches (`patches/oh.py:43-70`) parse whatever comes back; OH/NC
  decode CSVs via `r.text` (ISO-8859-1 guess → mojibake that mints new dedupe
  keys). MA does this correctly; copy its pattern.
- **S6 — MN Wayback fallback** serves an arbitrarily stale snapshot at debug
  log level (`custom/mn.py:97-119`) — months-old link lists look like "ok"
  runs. Log at warning, record snapshot age, fail beyond the staleness window.
- **S7 — TX header trim** leaves data rows wider than the header
  (`patches/tx.py:100-104`); truncate rows to header length.
- **S8 — No retry/backoff anywhere** except GA; every fetch is a single
  attempt, and one blip opens a GitHub issue (streak ≥ 1). A shared 2–3
  attempt helper + requiring streak ≥ 2 for issues would cut noise.

**LOW**: TX `_get_year` assert can crash on a year-less filename
(`patches/tx.py:198-202`); MA case-sensitive `/doc/` split
(`custom/ma.py:85,112`); BOM breaks header comparison
(`verify/harness.py:161-164` — use `utf-8-sig`); backfill conflates 404 with
transient errors (`backfill/github_flow.py:31-37`); BLN download has no
integrity check; `yaml.safe_load` accepts duplicate state keys silently.

---

## 3. Normalization, dedupe, links, storage, export

Overall: strong design discipline (link-never-merge, versioned ingest,
provenance everywhere). The three structural risks: the dedupe key's nullable
fields, `links.rebuild()` wiping manual links, and within-batch collapse
discarding amendments.

**HIGH**

- **A1 — Dedupe key collapses distinct notices when `notice_date` or
  `location` is null.** `engine.py:110-119`: for undated sources (GA, NJ/PA
  backfills) two genuinely different filings hash to one key; the second
  arrives as a "version" of the first, silently overwriting headcounts and
  vanishing from all counts, indistinguishable from a real amendment. Fix:
  fold `effective_date` into the key when `notice_date` is null; report
  collisions whose effective dates differ beyond the amendment window.
- **A2 — `links.rebuild()` deletes ALL notice links,** including the manual
  `date-repair`/`text-cleanup` links that `repair-dates` and `clean-text`
  insert and that `detect()` cannot reproduce (`links.py:264-274` vs
  `cli.py:388-394, 929-935`). The next `warnlive dupes` run destroys them —
  an active data-loss bug with a one-line fix
  (`DELETE ... WHERE method NOT IN (...)`).
- **A3 — Within-batch duplicate keys discard later, different rows.**
  `dedupe.py:53-56`: a source file listing an original row and an amended row
  under the same key drops the amendment forever instead of versioning it
  (and `test_dedupe.py:60-66` enshrines the lossy behavior). Fix: route
  differing-hash within-batch duplicates through the update path.

**MEDIUM**

- **A4 — `_fold` strips corporate suffixes from locations too**
  (`engine.py:116,192-194`): "Jefferson Co" ≠ "Jefferson County" as keys.
  Use a location-specific fold.
- **A5 — Synthetic dates presented as real**: NJ's reconstructed
  first-of-month notice dates and MN's Layoff-Start fallback
  (`custom/nj.py:37-48`, `custom/mn.py:23-25`) are indistinguishable from
  filed dates; they feed keys, charts, and the "undated" metric. Record a
  precision/inferred marker.
- **A6 — No upper-bound sanity on `jobs`** at normalize time
  (`engine.py:85`) — one upstream date-in-the-jobs-column row can dominate
  national totals (the regression gate's 100k ceiling catches only the
  grotesque case; see also O4).
- **A7 — `source_url` never refreshed on the unchanged path**
  (`dedupe.py:10-20`): notices first seen via Wayback keep archive URLs
  forever after the live scraper re-observes them; the `archived` metric
  overstates permanently.
- **A8 — Source corrections cause false splits**: a state correcting a date
  or location in place mints a new key; the stale sibling lingers, and the
  fuzzy detector's block key (same `loc_fold`, same date —
  `links.py:232-236`) cannot see exactly this case. A periodic
  stale-sibling sweep feeding `possible_duplicate` would cover it.
- **A9 — Marker/declared link detectors have no recency window**
  (`links.py:180-190`): "(Amended)" in 2026 can link to a 2019 filing at a
  floor score of 0.95. Bound candidates by ~12 months or fold date proximity
  into the evidence score.
- **A10 — Schema "migrations" are re-running schema.sql** (`db.py:29-32`) —
  additive-table-only, silently a no-op for added columns, version stamped
  regardless. Adopt a real migration list before the first column change.
- **A11 — Site export crashes on NULL `layoff_type`**
  (`site_export.py:143,439` — KeyError on anything outside the closed set)
  and **assumes enrichment homogeneity within an employer group**
  (`site_export.py:543` takes `rows[0]`'s CIK-era identity for the group).

**LOW**: `re.IGNORECASE` makes the trailing-address cut match ", at ..."
(`engine.py:227`); `_clean_text` truncates at the first `<` (`engine.py:145`);
roman-numeral site tokens misfire on "Li", "Xi" (`links.py:106,116`);
`observed_at` is date-granular (`pipeline.py:159`); repaired versions embed
internal fields in `fields_json` (`cli.py:400-402`); notice-key prefix length
can change between builds, breaking bookmarked URLs (`site_export.py:115-124`);
non-atomic site writes (`site_export.py:634-637`); `notice_links`/`notice_versions`
FKs lack `ON DELETE CASCADE`.

---

## 4. Non-LLM enrichment (enrich/)

Overall: the "ambiguity matches nothing" rule is genuinely enforced, and
provenance-basis strings substitute well for numeric confidence. Weaknesses
cluster in the name-only tiers, one industry-code bug, and refresh paths that
record transient failures as permanent answers.

**HIGH**

- **E1 — Wikidata label match can override a CIK identity in
  `employer_key`.** `annotate.py:200-208, 262-267`: when a CIK matched but has
  no Wikidata-by-CIK row, the name-keyed label tier (the weakest matcher) can
  set the QID, overwrite `canonical_name`/`parent_company`, and — because the
  key loop prefers `qid` over `cik` — regroup a strongly-identified employer
  under the wrong entity. Fix: skip the label tier when a CIK matched, or
  never let a label-basis QID outrank a CIK in the key.
- **E2 — SIC codes colliding with NAICS sector prefixes are accepted as
  NAICS at the most-trusted basis.** `industry.py:216-224`: the
  mislabeled-SIC guard only rejects codes whose 2-digit prefix is invalid
  NAICS. SIC 22xx (textiles) reads as Utilities, 23xx (apparel) as
  Construction, 42xx (trucking) as Wholesale, 48xx (communications) as
  Transportation — all recorded as `basis="source"` and propagated to the
  employer's other notices by `prime()`. Fix: validate 4-digit codes from
  known-mixed vintages against a real NAICS code list, or prefer the SIC
  crosswalk when a code is valid under both and predates NAICS adoption.

**MEDIUM**

- **E3 — GLEIF "uniqueness" is uniqueness within a 10-result page**
  (`gleif.py:50-62,105`) — check `meta.pagination.total` and refuse when it
  exceeds the page. Nonprofit matching can hand a for-profit filer a
  same-named exempt org's EIN + NTEE-derived industry (`nonprofits.py:132`).
- **E4 — EDGAR matcher soft spots** (also the LLM layer's floor, §1.7):
  weak-word guard is single-word-only (`edgar.py:296-309`); `_post_era_cik`
  relaxes forward without bound (`edgar.py:311-329`); no franchise-brand
  handling anywhere — bare "McDonald's" filings match the franchisor's CIK.
  Fix: extend weak-word to multi-word names with many extensions; cap
  post-era relaxation (~15–20 years, beyond → review); maintain a small
  franchise-brand list that routes to review.
- **E5 — Exhibit 21 lookup is era-blind and reads only the latest 10-K**
  (`subsidiaries.py:83-101,275-292`): a 2003 notice gets ownership as of the
  parent's most recent annual report; divested subsidiaries are invisible.
  Expose `source_year` to consumers; ideally crawl one older annual too.
- **E6 — Refresh paths record transient failures as permanent answers**:
  `subsidiaries.refresh` writes the done-marker even when the fetch failed
  (`subsidiaries.py:196-224`), permanently excluding the registrant;
  `edgar.refresh` swallows historical-quarter failures at debug level
  (`edgar.py:143-145`), silently shifting era spans and overwriting the good
  committed reference; GLEIF misses are permanent with no `retry_misses`
  flag at all (`gleif.py:65-66`). No reference file records when it was
  built; `edgar.refresh` hard-codes `last_year=2026`.
- **E7 — No retry logic in any fetcher**; the Wikidata SPARQL refresh is one
  unpaginated query against a service with a 60 s public timeout
  (`wikidata.py:33-57`); GLEIF pacing (~100 req/min) exceeds the documented
  60/min free tier (`gleif.py:51`).

**LOW**: override-CIK path skips the wikidata-by-CIK join, so the strongest
identities are decorated by the weakest matcher (`annotate.py:146-176`);
`review.build` surfaces only EDGAR candidates — GLEIF/nonprofit/Wikidata
ambiguity sets are discarded (`review.py:141-158`); multi-code NAICS lists
keep only the first code (`industry.py:19`); `prime()` treats "72" vs
"722310" as conflict (`annotate.py:105-107`); Wikidata alias-match with no
English label loops forever un-recorded (`wikidata.py:241,255`); filed-county
contradiction falls back instead of refusing (`places.py:750-754`).

---

## 5. Orchestration, CI, verification, repo hygiene

Overall: the two-layer verification design (per-scrape harness gating ingest,
whole-DB regression gating publish) is well-conceived; gates encode real
postmortems; deploy ordering means a failed gate leaves repo and site
untouched. Three doors undo much of it:

**HIGH**

- **O1 — The ad-hoc workflow bypasses the regression gate and the
  concurrency group.** `scrape-adhoc.yml:39-51` ingests and commits with no
  `check-regressions`, no dupes pass, no snapshot update, and no
  `concurrency: scrape`. The exact bug class the gate exists for can land on
  main via an ad-hoc run — after which the daily's whole-DB ceiling check
  fails *every* subsequent run until a human intervenes. Fix: add the
  concurrency group and run dupes + gate before the commit step.
- **O2 — The snapshot ratchet: warn-level drift re-baselines daily.**
  `check-regressions` exits 1 only on `failed`, but `--update-snapshot`
  still runs on `degraded` — so 2%/day drift never accumulates to any
  break threshold; the comparison base moves with the corruption
  (`verify/regression.py` + `cli.py:979-981`). Fix: update the snapshot only
  on fully-`ok` verdicts, or keep a longer-horizon anchor snapshot.
- **O3 — Commit-then-rebase over a binary DB.** All three workflows commit
  then `git pull --rebase` then push. A conflict on `warn.sqlite.gz` is
  unmergeable (run's output discarded); worse, a *successful* rebase keeps
  the runner's pre-run DB blob, silently dropping the concurrent commit's DB
  changes while its message survives in history. And each publish adds a
  ~22 MB non-delta-compressible gzip blob — ~5–6 GB/year of pack growth
  (local `.git` already 1.4 GB). Best single fix: commit the DB as
  `sqlite3 .dump | gzip --rsyncable` — text deltas well, diffs meaningfully,
  and makes conflicts mergeable in principle. Also: fail-don't-rebase on
  non-fast-forward.

**MEDIUM**

- **O4 — Regression gate value-level blind spots**: worker-total *shrinkage*
  is unchecked (only >5× growth); `employers` distinct-count and
  `first`/`last` dates are collected but never compared; a column swap
  putting non-null garbage in `location` passes. Cheap wins: symmetric
  shrink check, employer-count comparison, bounded date movement.
- **O5 — Zero-parsed-dates is silent**: `harness.py:125-145` skips both
  `date_sanity` and `freshness` when no dates parse — a transformer that
  breaks every date can verdict `ok`. Emit a fail when records exist but no
  dates parsed.
- **O6 — Failure telemetry shares fate with the data**: `state_runs` lives
  in the same SQLite that only persists on successful commit — runs that die
  at the gate or rebase contribute nothing to any streak, so a two-week
  failure can show streak 0. Regression-gate trips and install failures
  produce no GitHub issue at all (only per-state fetch failures do).
- **O7 — Exports and health are written before the gate runs**
  (`cli.py:112-114` vs the separate `check-regressions` invocation): locally,
  bad artifacts sit in `data/` looking legitimate. Fold the gate into
  `scrape` or gate before export.
- **O8 — `site/dist` is tracked in git but never regenerated by CI** — 327
  permanently-stale files, pure conflict fodder (currently showing as local
  modifications). `.gitignore` covers the build *input* but not the output;
  add `site/dist/` and `git rm -r --cached`.

**LOW**: Pages deployments race across two concurrency groups
(`deploy-site.yml` vs the scrape group); `test.yml` double-runs PRs and runs
on bot data commits (add `paths-ignore`); `report --gh-issues || true`
swallows alerting failures; the `skipped` verdict is declared but never
produced; review CSVs re-sort by rank weekly, churning full-file diffs.

---

## 6. Test coverage gaps (consolidated)

- **No test for A2** (rebuild wiping manual links) — a one-liner would have
  caught an active data-loss bug.
- **Zero tests** for `gleif.py`, `wikidata.py`, `subsidiaries.py`, and the
  nonprofit name+state join; the EDGAR fuzzy tier, `_tokens_compatible`, and
  `_suffix_cik` are untested; `annotate.py`'s tier-integration branches
  (including E1) are untested.
- **E2's colliding-prefix case** is exactly the one the existing
  mislabeled-SIC test does not cover.
- `export.py` (CSV export) has zero tests; engine functions with the most
  intricate regex logic (`base_employer`, `_clean_text`,
  `_classify_from_raw`) have no direct tests; subclassed state normalizers
  (ky/nj/ny/ri) are untested; no test pins the FNV shard function that must
  stay bit-identical to the SPA's copy.
- No test for the snapshot ratchet (O2) or the wrong-non-null-value case
  (O4).
- The adjudicate tests (`test_adjudicate.py`) are the best in the repo —
  each encodes a real design rule — but there is no test for the confirm
  stage's zero-corroborator acceptance being distinguishable downstream, nor
  for stance-misrouting (1.1), because those behaviors are design choices
  rather than bugs; if the redesign in 1.1 happens, its tests should pin the
  new contract.

---

## 7. Priorities

If you fix ten things, fix these, roughly in order:

1. **A2** — `links.rebuild()` wiping manual links (one line; active data loss).
2. **O1** — ad-hoc workflow bypassing the gate + concurrency group (the open
   door around every guardrail).
3. **1.1** — remove the stance router; always collect proposals; reserve
   permanent rejections for humans or cross-model agreement. Run the ledger
   replay experiment first to size the win.
4. **1.2 + 1.3** — require a CIK-anchored corroborator; make confirm a
   *supplement* to corroboration (different model), not a substitute; carry
   the basis distinction downstream.
5. **O2** — stop re-baselining the snapshot on warn verdicts.
6. **A1 + A3** — dedupe-key null collapse and within-batch amendment loss.
7. **E2** — the SIC/NAICS prefix-collision bug (silently mislabeling at the
   most-trusted basis today).
8. **O3** — switch the committed DB to an rsyncable text dump; fail on
   non-fast-forward instead of rebasing binaries.
9. **E1** — label-tier QID displacing CIK identities in `employer_key`.
10. **S1 + S4** — the dispatcher import fallthrough, and escalation for
    chronically degraded states.

Cross-cutting: add small shared retry helpers (fetch layer and enrichment
refreshes both lack any), stamp reference files with their build date, and
give the LLM layer a measured precision number for identity the way industry
already has one.
