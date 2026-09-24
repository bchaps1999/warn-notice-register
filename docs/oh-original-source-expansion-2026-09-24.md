# Ohio original-source expansion, 2026-09-24

This isolated source-only candidate extends the [Missouri candidate](agency-mo-expansion-2026-09-24.md)
with Ohio Department of Job and Family Services WARN tables for 2015–22.
The [pinned source manifest](../data/source_snapshots/oh/official-2015-2025/manifest.json)
records each original agency URL, capture URL, exact byte count, and SHA-256.
The 2015–17 tables are agency PDFs preserved by the Internet Archive; the
2018–19 PDFs are directly hosted by Ohio; the 2020–22 agency HTML tables are
preserved by the Internet Archive. These sources are the agency's own listings,
not a third-party reconstruction. Some annual tables contain older notice IDs,
so table year is provenance rather than a guaranteed filing year.

The eight files contain **898 physical notice rows**. The projection admits
**854** records with distinct agency notice IDs and holds **44** observations:
25 superseding amendment rows, six with conflicting original IDs, two with
invalid or multiple IDs, and 11 whose PDF continuations name additional
sites or worker counts. The latter stay held until a site-complete projection
can avoid understating workers or double-counting a displayed total. Every
held row and its full continuation text has a source pointer in the exception
ledger. The admitted rows report **114,314 affected workers**; 20 have no
usable worker count, and 255 have no single unambiguous action day.

The tables' `Date Received` is an **agency receipt date**, retained in
`source_details`, not assigned to the legal employer notice date. The latter
is null for all added rows. A simple `Layoff Date(s)` value is stored as a
reported action date. A range or narrative does not become a guessed single
day. Notice IDs determine source identity; duplicate and revised observations
do not mint extra notices. The projector reads revision markers even when
they appear on PDF continuation lines.

The staged bundle is
[2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.tar.gz](../data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.tar.gz),
with SHA-256 `858d1493a86f2a8012f2ce50844b316ab36731ee4482987a93893baedc457de7`.
It has 2,016 source files. The candidate is isolated; the checked-in public
database and site have not been replaced.

The replay adds 854 Ohio notices and 114,314 reported affected workers to
the Missouri candidate, yielding **71,490 notices**, **79,261 versions**,
and **7,482,177 workers** overall. Ohio has **2,438** notices. Database
integrity is `ok` with zero foreign-key errors. The
[replay report](../data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1-report.json)
and [11,888-row exception ledger](../data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-oh-v1.exceptions.jsonl.gz)
preserve the full row accounting.
Two isolated replays matched counts, notice and version fingerprints, the
exception ledger SHA-256
`0d636c41b43273a6a12c8507652ca57a42b4c266403bdb6c3882e57ce705a34c`,
and the SQLite file SHA-256
`e55b85252e7bb5f6ce2bf9ca1083f10bc962c192f7872a761687ad15544cc8cb`.
The report differs only in temporary input and output paths. The preserved
compressed exception ledger SHA-256 is
`3ab3a8c5610e7f36a7cb49a39bfff9fb18235b5d1406881f4f8100e4d1aa1c52`.

Ohio's remaining large date gap is **2023–25**. Ohio hosts individual filings
in year folders, including a [2023 GXO filing](https://dam.assets.ohio.gov/image/upload/jfs.ohio.gov/warn/WARN%202023/GXO.pdf)
and a [2025 Ohio Recovery Center filing](https://dam.assets.ohio.gov/image/upload/jfs.ohio.gov/warn/WARN%202025/OhioRecoveryCenter.pdf),
but this pass did not establish a complete agency index or annual table for
those years. Individual documents are promising for targeted recovery; they
cannot establish complete annual coverage by themselves. The older 2001–14
archive and current 2026 capture remain in the candidate. A future agency
records request or archived annual index could close 2023–25 and resolve the
held multi-site observations.
