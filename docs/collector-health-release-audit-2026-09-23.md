# Collector health and relaunch disposition

This audit reads the latest `state_runs` in the current local `data/warn.sqlite` and the current code. These statuses belong to the old live collection database, not to the isolated v10 source-only candidate. A historical notice count does not prove that a collector is working today.

The later [agency-only policy](agency-only-rebuild-2026-09-23.md) pauses the
GA, IA, KY, OR, and TN upstream collectors before fetch because they append
BLN-hosted historical data. Their saved health statuses below predate that
pause and cannot be used as evidence of current collector availability.
Agency-only adapters are a release gate for resuming those scheduled states.

| State | Latest saved run | Cause from saved check | Relaunch work |
| --- | --- | --- | --- |
| FL | Failed, September 22; ten-run failure streak | Upstream scraper raises `IndexError` while taking the first year from an empty page link lookup. The agency still exposes year-addressed REACT endpoints. | A [direct-year patch](fl-collector-repair-2026-09-23.md) reproduces all 3,230 saved cached rows by content. Run a fresh networked capture and verifier before clearing the failed status; keep the last successful capture visible until then. |
| MA | Failed, September 22; ten-run failure streak | The official index request returned HTTP 403. A [September 23 live probe](ma-collector-probe-2026-09-23.md) from this environment reached the index, fetched five workbooks and one weekly CSV, and passed normalization/verification on 392 rows. The saved run does not establish whether the Zyte fallback was configured or successful. | Recheck access on the scheduled runner before clearing its failed status. The live capture contains a Sodexo label revision already represented as two notice IDs in the old database; resolve that source-record correspondence before ingesting the capture. Keep `RECEIVED` separate from legal notice. |
| HI | Failed, September 6; five-run failure streak | The old scraper follows a relative Cloudflare email link as if it were a yearly page. The agency also [moved current notices on August 1](hi-source-transition-2026-09-23.md). | An inactive adapter now filters yearly/WDD links and preserves receipt/effective roles. Complete the WDD capture and event/identity safeguards, then activate and verify a fresh run before clearing failed health. |
| CO | Degraded, September 22; ten-run degraded streak | The saved observed and expected CSV headers contain exactly the same 25 field names, only in a different order. | The verifier now treats harmless reordering as a pass while still warning about added/missing columns. Confirm with a fresh capture. |
| GA | Degraded, September 6; seven-run degraded streak | No stable expected-column snapshot is configured. The dynamic source form produces many optional columns; parse failures were zero in the saved run, but the health warning remains. | Define a stable required-column policy or preserve the degraded label, then check fresh source IDs and row correspondence. |

State pages now report the stored verdict (`OK`, `Degraded`, `Failed`, or `Unknown`) and show the last check and last successful date. They no longer call a degraded run “Current.” At release, run a fresh health check on the exact code commit and record which states are current, stale, blocked, or archive-only. The source-only candidate can be reproducible while live collectors are unhealthy; both facts must be visible.
