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


@pytest.mark.parametrize("state, field, cell, layoff_type, temporary", [
    ("DC", "Code Type", "1", "mass_layoff", None),
    ("DC", "Code Type", "2", "closure", None),
    ("DC", "Code Type", "3", None, None),
    # Maryland's numeric legend is the reverse of DC's.
    ("MD", "Type", "1", "closure", None),
    ("MD", "Type", "2", "mass_layoff", None),
    ("MD", "Type", "2 Temporary", "mass_layoff", 1),
    ("MD", "Type", "1 *Note-This is an update to the WARN", "closure", None),
    ("MD", "Type", "12", None, None),
    ("MD", "Type", "Mass Layoff - No Recall", "mass_layoff", None),
    ("MD", "Type", "Plant Closure", "closure", None),
    ("MD", "Type", "Temporary Furlough", None, None),
    ("MD", "Type", "Unsure at this time", None, None),
    ("WI", "Original Notice Type / Update Type", "CL", "closure", None),
    ("WI", "Original Notice Type / Update Type", "WR", "mass_layoff", None),
    ("WI", "Original Notice Type / Update Type", "CL, AW, LS", "closure", None),
    ("WI", "Original Notice Type / Update Type", "CL, WR", None, None),
    ("WI", "Original Notice Type / Update Type", "Unknown", None, None),
])
def test_agency_type_legends(state, field, cell, layoff_type, temporary):
    got = extract(state, {field: cell}, _rec())
    assert got.get("layoff_type") == layoff_type
    assert got.get("is_temporary") == temporary
    if layoff_type:
        evidence = json.loads(got["source_details"])["layoff_type_evidence"]
        assert evidence["source_text"] == cell
        assert evidence["type_code_legend_url"].startswith("https://")
        assert evidence["type_code"]


def test_wisconsin_update_tokens_are_recorded_without_changing_admission():
    got = extract("WI", {"Original Notice Type / Update Type": "CL, AW, LS"}, _rec())
    assert json.loads(got["source_details"])["update_types"] == ["AW", "LS"]
    rescinded = extract("WI", {"Original Notice Type / Update Type": "WR, RN"}, _rec())
    assert rescinded["layoff_type"] == "mass_layoff"
    assert json.loads(rescinded["source_details"])["update_types"] == ["RN"]
    both = extract("WI", {"Original Notice Type / Update Type": "CL, WR, OC"}, _rec())
    assert "layoff_type" not in both
    assert json.loads(both["source_details"])["update_types"] == ["OC"]


def test_type_code_outranks_a_conflicting_engine_reading():
    # The legend code is the agency's own classification of this notice.
    assert extract("DC", {"Code Type": "1"}, _rec("closure"))["layoff_type"] == "mass_layoff"


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


def test_md_legend_code_outranks_keyword_reading_of_the_same_cell():
    from warnlive.normalize.details import _add_type_code_evidence

    result, details = {}, {}
    _add_type_code_evidence(
        "MD", {"Type": "2 (Possibly turning into a closure)"},
        {"layoff_type": "closure"}, result, details,
    )
    assert result["layoff_type"] == "mass_layoff"
    assert details["layoff_type_evidence"]["replaced_keyword_reading"] == "closure"
