"""Historical same-key overlap audit never treats a key as field agreement."""

from warnlive.migrate.overlap_audit import differences


def test_differences_reports_only_canonical_field_disagreements():
    source = {
        "employer_name": "Food World", "location": "Birmingham",
        "notice_date": "2008-01-01", "effective_date": "2008-02-11",
        "effective_date_end": None, "employees_affected": 64,
        "raw_extra": "old row",
    }
    candidate = source | {
        "effective_date": "2009-02-11", "employees_affected": 72,
        "raw_extra": "new row",
    }
    assert differences(source, candidate) == {
        "effective_date": {"source": "2008-02-11", "candidate": "2009-02-11"},
        "employees_affected": {"source": 64, "candidate": 72},
    }
    assert differences(source, source | {"raw_extra": "different"}) == {}
