# Revision-aware admission candidate (2026-09-24)

This is an isolated rebuild from `data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.tar.gz`. The released database and source bundle were not changed. The figures below use the source-backed date-precision code and the revision-aware admission changes together.

## Admission rule

An agency filing ID groups observations. It does not, by itself, prove that different worksites or layoff phases are revisions of one notice. A labeled original is the canonical notice when available; an explicitly labeled amendment with the same ID is linked to it as revision evidence. Identical captures are linked without creating another notice. Conflicting unlabeled rows or uncertain site/phase allocation remain held. Where no filing ID exists, employer, site, notice date, and action date distinguish events; changed observations of the same apparent event remain possible revisions rather than automatically replacing a notice.

State adapters apply that rule to NY's ID-less annual dashboard, OH's published Notice IDs, OR's WARN numbers, and IA's event logs. The NY dashboard `Index` is not a filing ID. OH amendment counts are not assumed to be replacement totals; the original remains canonical and the amendment pointer is retained. IA source addresses are retained as unverified address evidence, not asserted as canonical worksites.

## Candidate effect

| State | Additional notices | Additional workers | Main reason |
| --- | ---: | ---: | --- |
| NY | 2,075 | 171,448 | Distinct annual dashboard events previously blocked by repeated employer names; null action dates or worker counts allowed when event identity is sufficient |
| IA | 399 | 29,444 | Distinct source-backed events previously blanket held |
| CA | 216 | 15,919 | Agency archive rows no longer blocked solely because another employer had a notice in the same month |
| FL | 83 | 11,560 | Same narrow employer/month overlap screen |
| OR | 1 | 140 | Identical historical agency capture coalesced by WARN number |
| **Total** | **2,774** | **228,511** | |

Candidate totals are 74,264 notices, 82,035 versions, and 7,710,688 workers, versus 71,490 notices, 79,261 versions, and 7,482,177 workers in the released bundle report. OH adds no notice or worker total: its 25 amendment observations are classified and linked to original source rows. The verified OH filing `011-20-126` remains at 186 workers rather than taking the incremental amendment count of 2.

The exception ledger falls from 11,888 to 9,114 rows. It records 3,794 held NY annual rows (3,023 possible multi-site filings, 671 unresolved correspondence with existing notices, 100 possible same-event revisions) and 536 held IA rows (including 263 amendments without a verified parent and 181 site/phase or worker allocation ambiguities). Sixty-one duplicate IA captures are linked to admitted notices in `source_observations`; they do not create extra notices. Relative to an earlier candidate replay, the date-precision normalization also coalesces three duplicate MN raw versions and one CO raw version, adding four corresponding exception rows.

## Other restrictions examined

The state-wide month exclusion in cached CA and FL agency records was broader than its duplicate-prevention purpose. It is now scoped to the same normalized employer and month. One CA row still overlaps on that narrower key.

The next largest holds do not have the same evidence for automatic release. NY's 3,023 multi-site rows can represent sites within one filing; its 671 existing-notice correspondences lack a dashboard filing ID. OR's 427 multi-site or phase rows need site/phase allocation. CA's 908 conflicting same-key cache rows have changed content without reliable revision order. MO's 1,284 rapid-response rows lack the row-level review/attestation used by that source. These should stay held pending source-level adjudication. Duplicate captures and other parsing exceptions are also counted in the exception ledger, so its total is not a count of missing notices.

## Verification

The isolated rebuild completed with `integrity_check=ok` and zero foreign-key errors. It retained source-row and disposition evidence in notice details or the exception ledger. Its notice and version fingerprints are `09d0921ae3156a21c3ce479ecd083e86e0c213409f2807b3dfa0c55ecd65572f` and `609807b5fd84b2548d0bb7e3e2b2985dd1c977649f14abf1a650ff1c335489a3`. Replay it with:

```bash
python -m warnlive.migrate.offline_rebuild \
  --bundle data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.tar.gz \
  --db /private/tmp/warn-revision-candidate.sqlite \
  --observed-at 2026-09-24 --source-only \
  --report /private/tmp/warn-revision-candidate-report.json \
  --exceptions /private/tmp/warn-revision-candidate-exceptions.jsonl
```

The public release was not updated by this change.
