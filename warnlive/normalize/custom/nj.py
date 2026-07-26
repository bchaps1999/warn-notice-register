"""New Jersey — subclass of warn-transformer's NJ transformer.

The source lists no notice date, only the posting month ("Month Posted":
bare month name, no year) and an effective date. Upstream ignores the month
entirely, so every NJ notice lands with a null notice_date. We reconstruct a
month-granularity notice_date (first of month) by taking the month name and
inferring its year from the effective date: same year, minus one when the
posted month is later in the calendar than the effective month (December
posting, January effective). Wrong only when a notice runs more than ~a year
ahead of its effective date, which WARN's 60/90-day horizon makes rare.
"""

from __future__ import annotations

import re

from warn_transformer.transformers.nj import Transformer as UpstreamTransformer

_MONTHS = {
    name: i + 1
    for i, name in enumerate(
        "january february march april may june july august september october november december".split()
    )
}


def _infer_notice_date(row: dict) -> str:
    month = _MONTHS.get((row.get("Month Posted") or "").strip().lower())
    eff = (row.get("Effective Date") or "").strip()
    if not month or not eff:
        return ""
    m = re.search(r"(\d{4})-(\d{2})-\d{2}", eff)
    if m:
        year, eff_month = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d{1,2})/\d{1,2}/(\d{2,4})", eff)
        if not m:
            return ""
        eff_month, year = int(m.group(1)), int(m.group(2))
        if year < 100:
            year += 2000
    if not (1 <= eff_month <= 12 and 1990 <= year <= 2100):
        return ""
    if month > eff_month:
        year -= 1
    return f"{year:04d}-{month:02d}-01"


class Transformer(UpstreamTransformer):
    """Transform New Jersey raw data for consolidation."""

    fields = dict(
        UpstreamTransformer.fields,
        notice_date=_infer_notice_date,
    )
    date_format = [*UpstreamTransformer.date_format, "%Y-%m-%d"]
