"""layoff_type / is_temporary read from labeled source columns
(warnlive.normalize.details._add_source_type_evidence)."""

import json

import pytest

from warnlive.normalize.details import extract


def _rec(layoff_type="unknown", is_temporary=None):
    return {"layoff_type": layoff_type, "is_temporary": is_temporary}


@pytest.mark.parametrize("perm, temp, furl, expected", [
    ("", "14", "", 1),          # temporary losses only
    ("", "", "196", 1),         # furloughs only
    ("1", "0", "0", 0),         # permanent only
    ("12", "", "", 0),
    ("3", "2", "4", None),      # a mix is neither
    ("0", "0", "0", None),      # nothing lost
    ("", "", "", None),         # nothing reported
    ("5*", "", "", None),       # unreadable cell
])
def test_colorado_temporary_from_job_loss_columns(perm, temp, furl, expected):
    raw = {"permanent_job_losses": perm, "temporary_job_losses": temp, "furloughs": furl}
    got = extract("CO", raw, _rec())
    assert got.get("is_temporary") == expected
    details = json.loads(got.get("source_details") or "{}")
    if perm or temp or furl:
        losses = details["job_losses"]
        assert losses["rule"] == "co_job_loss_columns_v1"
        if perm.isdigit():
            assert losses["permanent"] == int(perm)
    else:
        assert "job_losses" not in details


def test_colorado_keeps_an_existing_reading():
    raw = {"permanent_job_losses": "", "temporary_job_losses": "14", "furloughs": ""}
    assert "is_temporary" not in extract("CO", raw, _rec(is_temporary=0))


@pytest.mark.parametrize("state, raw, layoff_type, temporary", [
    ("IN", {"Notice Type": "LO"}, "mass_layoff", None),
    ("IN", {"Notice Type": "RH"}, None, None),
    ("RI", {"Closing Yes/No": "No"}, "mass_layoff", None),
    ("RI", {"Closing Yes/No": ""}, None, None),
    ("MI", {"action": "Temporary layoff"}, "mass_layoff", 1),
    ("MI", {"action": "Permanent layoff"}, "mass_layoff", 0),
    ("MI", {"action": "Layoff"}, "mass_layoff", None),
])
def test_labeled_type_columns(state, raw, layoff_type, temporary):
    got = extract(state, raw, _rec())
    assert got.get("layoff_type") == layoff_type
    assert got.get("is_temporary") == temporary
    if layoff_type:
        evidence = json.loads(got["source_details"])["layoff_type_evidence"]
        assert evidence["source_text"] == next(iter(raw.values()))


def test_type_column_never_overrides_the_engine():
    assert "layoff_type" not in extract("IN", {"Notice Type": "LO"}, _rec("closure"))


def test_iowa_typed_cells_yield_the_industry_column():
    from warnlive.enrich.industry import industry_from_fields_json

    cells = [{"type": "string", "value": v} for v in (
        "Marriott Hotel Services, Inc", "300 E 9th St", "Coralville", "Johnson", "IA")]
    cells += [{"type": "integer", "value": 52241}, {"type": "string", "value": "Closing"},
              {"type": "integer", "value": 96},
              {"type": "datetime", "value": "2021-06-17T00:00:00"},
              {"type": "datetime", "value": "2021-08-17T00:00:00"},
              {"type": "string", "value": "East Central Iowa"},
              {"type": "string", "value": "Accommodation and Food Services"}]
    fields = json.dumps({"raw_extra": json.dumps(cells)})
    assert industry_from_fields_json(fields) == (
        "Accommodation and Food Services", "72", "sector-name")
    # Another list layout (New York's dashboard cells) has no industry.
    ny = json.dumps({"raw_extra": json.dumps(["Acme", "2020-05-01"] + [""] * 10)})
    assert industry_from_fields_json(ny) == (None, None, None)
