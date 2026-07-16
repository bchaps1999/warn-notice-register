"""Claims-week calendar math.

Conventions:
- A "claims week" is identified by its week-ending Saturday (DOL convention).
- The advance print for week w is normally released the following Thursday
  (w + 5 days) at 8:30 ET; on federal-holiday weeks the release shifts
  (usually to Wednesday). For backtest target alignment we do NOT rely on a
  holiday table: the true release date is recovered from the ALFRED vintage
  itself (see ingest.alfred). The nominal schedule here is used for
  enumerating forecast origins and for live scheduling.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

SATURDAY = 5  # date.weekday() value
THURSDAY = 3


def claims_week(d: date) -> date:
    """Week-ending Saturday of the claims week containing d."""
    if isinstance(d, datetime):
        d = d.date()
    return d + timedelta(days=(SATURDAY - d.weekday()) % 7)


def prev_claims_week(week_ending: date, n: int = 1) -> date:
    return week_ending - timedelta(weeks=n)


def nominal_release_date(week_ending: date) -> date:
    """Nominal Thursday on which the advance print for `week_ending` lands.

    Holiday weeks may shift this a day earlier; use ALFRED vintage dates as
    ground truth for historical alignment.
    """
    if week_ending.weekday() != SATURDAY:
        raise ValueError(f"{week_ending} is not a Saturday")
    return week_ending + timedelta(days=5)


def canonical_origin(week_ending: date) -> datetime:
    """Canonical backtest forecast origin for target week `week_ending`:
    Wednesday 23:59 ET before the nominal Thursday release."""
    release = nominal_release_date(week_ending)
    return datetime.combine(release - timedelta(days=1), datetime.min.time()).replace(
        hour=23, minute=59
    )


def weekly_saturdays(start: date, end: date) -> list[date]:
    """All week-ending Saturdays in [start, end]."""
    first = claims_week(start)
    out = []
    w = first
    while w <= end:
        out.append(w)
        w += timedelta(weeks=1)
    return out
