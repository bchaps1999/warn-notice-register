# Missouri original-source expansion, 2026-09-24

This isolated source-only candidate extends the [Texas and Oregon candidate](agency-tx-or-historical-expansion-2026-09-24.md)
with the Missouri Office of Workforce Development's [2019–24 annual WARN tables](https://jobs.mo.gov/employer/warn)
and its agency-linked 1997–2018 workbook. The exact captures and source
rules are described in the [Missouri source review](mo-official-source-review-2026-09-24.md).
The annual source bytes are browser-rendered text, not raw HTML; the workbook
is the agency-published Excel file.

| Layer | Source rows | Admitted | Held | Main limit |
| --- | ---: | ---: | ---: | --- |
| Annual 2019–24 tables | 297 | 295 | 2 | One zero-affected row; one Kansas site |
| Historical workbook | 1,301 | 17 | 1,284 | Mixed rapid-response register; most rows have not been verified as WARN filings |

The annual tables have no stable filing IDs. `Received` is agency receipt,
not the legal employer notice date. Complex, tentative, and invalid action
dates remain null; blank site or worker counts remain null. The workbook's
`Date Rec’d` is also agency receipt. Every admitted row retains the source
observation pointer and raw fields. Every held row appears in the exception
ledger. The reviewed Penske row has an unknown canonical action day because
its comment says the original date moved forward without stating the new day.

The [combined source bundle](../data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-v1.tar.gz)
has **2,007 files** and SHA-256
`80dc5344f3cd126f81c59ae0ab103dbd687be5f80523bde63627ada2892be142`.
The staged candidate contains **70,636 notices**, **78,407 versions**, and
**7,367,863 reported affected workers**, adding exactly **312 Missouri notices**
to the prior candidate. Missouri now has **384** notices: 72 prior 2025–26
rows, 295 annual-table rows, and 17 reviewed workbook rows. Its 15 missing
action dates and 25 missing locations remain visible rather than imputed.
Integrity is `ok` with zero foreign-key errors. The
[replay report](../data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-v1-report.json)
and [11,844-row exception ledger](../data/source_snapshots/2026-09-24-agency-only-ny-ga-orhist-txhist-mo-v1.exceptions.jsonl.gz)
preserve the audit trail.
Two corrected isolated replays matched counts, notice and version
fingerprints, the exception ledger SHA-256
`f4896194a8840d02c912a23499f90453b5f75de6661b62284c5f52e73a0bec2c`,
and even the SQLite file SHA-256
`695016b260c8a8d8af85992b5334ef89b2bfa7b0de707c175d55aa988cf36420`.
The notice-content fingerprint is
`3815a91b92b00b3b8ddcc9d3c67942fc6a9470d2afa6c71578bd5739da2bb535`.

The largest Missouri gap remains the pre-2019 register. In particular,
1997–2004 has no admitted workbook rows, and most later workbook entries
still need underlying WARN notices or an agency record response to establish
filing status and resolve event details. This is an isolated candidate; the
public database and site have not been replaced.
