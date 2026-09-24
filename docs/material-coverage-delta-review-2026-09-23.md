# Material state deltas: source and key accounting

> Historical BLN-containing comparison. The current candidate and its larger
> agency-source gaps are in the [agency-only rebuild](agency-only-rebuild-2026-09-23.md).

This compares the current local `data/warn.sqlite` with the isolated v10 source-only candidate named in [the staged comparison](staged-release-comparison-2026-09-23-v10.md). It is a read-only diagnostic. The older database is a comparison point, not proof that its rows are valid WARN events. Exception counts are source rows, not canonical notice counts.

| State | Current → v10 notices | Key overlap | Main source explanation and unresolved release question |
| --- | ---: | ---: | --- |
| WA | 2,868 → 1,459 | 1,459 shared; 1,409 old-only | All old-only rows point to `esd.wa.gov`. The exception summary has 1,451 BLN rows marked `source_identity_unresolved`; these are not a one-to-one match with old-only notices. The retained current table's `Received Date` is agency receipt, so v10 clears all 1,459 formerly populated legal notice dates. Verify the held BLN years against preserved agency source material before claiming historical completeness. |
| KY | 1,760 → 806 | 806 shared; 954 old-only | Current-source `date_received` is agency receipt. V10 keeps the 806 current-source keys and excludes BLN historical keys without established filing identity or legal notice role. The exception summary has 1,855 BLN source rows in `source_identity_unresolved`, including duplicates/overlap. Source-backed lineage is still needed for the old-only records. |
| IL | 5,710 → 4,863 | 4,863 shared; 847 old-only | This v10 comparison used mutable employer/date/place keys and conflated eight pairs of distinct IEBS records. The later [IEBS-key replay](il-source-record-transition-2026-09-23.md) represents all 4,871 frozen export IDs, but still holds unmatched BLN history and lacks a fresh source capture. The exception summary has 1,074 BLN rows in `unmatched_after_conservative_fill` plus 128 superseded BLN rows. Old-only keys are not a count of valid lost filings. |
| KS | 2,810 → 2,206 | **80 shared keys; 2,730 old-only keys; 2,126 new-only keys** | This is substantial key migration, not just 604 missing records. V10 keys 2,126 rows by validated Kansas record number (`KS:<record_number>`); old keys used mutable field content. Matching the separate `source_notice_id` finds **1,929 shared source IDs** with identical employer and notice date, 881 old-only IDs and 259 new-only IDs. The candidate has 2,188 distinct source IDs across 2,206 rows; 15 IDs repeat across distinct agency record numbers, such as Boeing IDs 30/31/37/45. This confirms why a content-derived ID is insufficient to merge those rows, but distinct record numbers alone do not establish separate layoffs. The exception summary has 2,122 unmatched BLN rows and 154 superseded BLN rows. Reconcile the remaining IDs and repeated agency records before claiming the net difference is all lost events. |
| IA | 965 → 375 | 375 shared; 590 old-only | The preserved official event log and 2023 PDF have 573 and 362 source rows respectively marked `official_iowa_identity_unresolved`; BLN has 985 unresolved source rows. The old-only rows cluster in 2018–2026. Resolve official row-to-filing identity or publish a clear held-source limitation; do not restore them by name/date similarity. |

The exception counts above come from [the v10 source exception summary](source-exceptions-by-state-source-year-v10.csv). The Kansas source-ID comparison is a diagnostic bridge: that ID is a content-derived field in older rows and can repeat, so it does not establish filing identity on its own. Exact matching needs the row-level exception ledger and preserved raw artifacts. The large Kansas key churn is a separate release gate from its net count change. This table does not decide to admit or remove any row.

The later Illinois IEBS transition is documented separately because it deliberately changes Illinois keys. Its 4,871-record candidate should not be compared to the old database by key overlap alone; use the [version-level IEBS bridge](il-old-to-new-source-records-2026-09-23.csv). The frozen Illinois raw export is stale relative to the current local database, so the remaining coverage gap cannot be closed by key migration alone.

## Direct correspondence to the row-level exceptions

Joining each old-only record's `source_notice_id` to the v10 exception ledger explains much of the apparent loss without treating row counts as event counts. This is diagnostic because an ID can repeat or change when source text changes.

| State | Old-only source IDs | Exception reason matches (may overlap) | IDs without a direct exception-ID match |
| --- | ---: | --- | ---: |
| WA | 1,409 | 1,332 unresolved identity; 33 superseded | 44 |
| KY | 954 | 950 unresolved identity | 4 |
| IL | 847 | 829 unmatched after conservative fill | 18 |
| KS | 2,730 | 1,989 unmatched after conservative fill; 40 superseded | 701 |
| IA | 590 | 576 unresolved identity; 8 conflicting same key | 14 |

An ID can match more than one exception reason; Iowa's eight `conflicting_same_key` IDs also have unresolved-identity matches. The unmatched tails require source-level inspection; they must not be silently counted as valid lost filings or dismissed as harmless hash drift. For Kansas, many old-only *keys* belong to records still present under new keys, so its 701 unmatched IDs cannot be added to the 604 net count difference.
