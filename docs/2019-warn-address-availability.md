# 2019 WARN address availability — corrected against `data/exports/warn_notices.csv`

**Supersedes my earlier draft, which said no state had 2019 street addresses. That was wrong.** It was based on researching state landing pages from scratch, without checking this repo. The pipeline here already has the answer.

## Answer

Of **3,300** rows in the corpus with a 2019 notice date, **243 (~7%) carry a street-level address** in `location`.

**231 of those 243 come from four states:**

| State | 2019 address example | Source the pipeline uses |
|---|---|---|
| **CT** | `84 Deerfield Lane, Meriden CT 06450` | `dolpublicdocumentlibrary.ct.gov/CsblrCategory?prefix=/rapid_response/warn_documents` |
| **IL** | `130 E. Randolph Street Chicago, IL 60601` | `apps.illinoisworknet.com/iebs/api/public/export` |
| **MD** | `1508 John Avenue Baltimore, MD 21227` | `dllr.state.md.us/employment/warn.shtml` |
| **NC** | `5310 S Alston Avenue Durham NC 27713` | `commerce.nc.gov/data-tools-reports/labor-market-data-tools/workforce-warn-reports` |

The remaining ~12 rows are a scattered tail worth a closer look.

## Why my from-scratch research got this wrong

Every one of the four is a state I had *checked* and written off. The failure was consistent: **I researched what each state says on its public landing page; the pipeline uses the actual data endpoint.** Landing-page prose systematically understates what's available.

- **CT** — I called it "stale, only 2018 visible" from the legacy `ctdol.state.ct.us` page. There is in fact a live **document library** on a different host.
- **IL** — I called it "summary details only," quoting DCEO's own page verbatim. There is a **public JSON API** on IllinoisWorkNet returning full addresses.
- **MD / NC** — I marked both as annual-log/summary states without inspecting actual records.

Corollary worth carrying forward: the earlier 50-state landscape report is likely **too pessimistic in the same direction** for other states. Its per-state verdicts should be re-derived from this pipeline's sources, not from agency landing pages.

## Related correction: California

CA current-era rows *do* carry street addresses (`932 West Mill Street San Bernardino CA 92410`) — from the rolling `warn_report1.xlsx`. The archival annual PDFs I checked carry only City + County, which is why CA has no 2019 addresses despite having them today. Both facts are true; the archive is simply thinner than the live file.

## Schema constraint

`warn_notices.csv` has a single free-text `location` column — no structured `street` / `city` / `state` / `zip` fields. So addresses are present but not parsed, and formatting varies by source:

- `"84 Deerfield Lane, Meriden CT 06450"` — comma before city
- `5310 S Alston Avenue Durham NC 27713` — no commas at all
- `"1020 Olympic Dr. 950 Raddant Rd. Batavia, IL 60510"` — **two street addresses in one field**
- `"Chicago O'Hare Airport 10000 W Balmoral Ave. Chicago, IL 60666"` — venue name prefixed

If address-level analysis matters, the next step is a parsed-address migration (`usaddress` or `libpostal`) with a `geocodable` flag, rather than more scraping.

## Unrelated but visible in `health.md`

- **KY, MI, MS** are failing on `ModuleNotFoundError: No module named 'pyquery'` — a one-line dependency fix recovering three states.
- **GA, LA, MO** marked `broken`; **AR, NH, WV, WY** are `manual_only` (zero notices).
- **CO** is `degraded` with `schema_drift`.
