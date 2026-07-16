from datetime import date, datetime

from uiforecast.calendar import (
    canonical_origin,
    claims_week,
    nominal_release_date,
    weekly_saturdays,
)


def test_claims_week_maps_to_saturday():
    # 2024-06-03 is a Monday -> week ends Saturday 2024-06-08
    assert claims_week(date(2024, 6, 3)) == date(2024, 6, 8)
    # a Saturday maps to itself
    assert claims_week(date(2024, 6, 8)) == date(2024, 6, 8)
    # a Sunday starts the next claims week
    assert claims_week(date(2024, 6, 9)) == date(2024, 6, 15)


def test_nominal_release_is_following_thursday():
    # week ending Sat 2024-06-01 -> released Thu 2024-06-06 (verified vintage)
    assert nominal_release_date(date(2024, 6, 1)) == date(2024, 6, 6)


def test_canonical_origin_is_wednesday_night():
    o = canonical_origin(date(2024, 6, 1))
    assert o == datetime(2024, 6, 5, 23, 59)
    assert o.weekday() == 2  # Wednesday


def test_weekly_saturdays_span():
    weeks = weekly_saturdays(date(2024, 1, 1), date(2024, 1, 31))
    assert weeks[0] == date(2024, 1, 6)
    assert weeks[-1] == date(2024, 1, 27)
    assert len(weeks) == 4
