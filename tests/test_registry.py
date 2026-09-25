import pytest
import yaml
import json

from warnlive.registry import load_registry
from warnlive.verify.harness import _source_date


def test_registry_loads_and_validates():
    reg = load_registry()
    assert len(reg.all()) == 52  # 50 states + DC + PR
    manual = [c for c in reg.all() if c.source == "manual"]
    assert sorted(c.postal for c in manual) == ["ar", "nh", "pr", "wv", "wy"]
    custom = [c for c in reg.all() if c.source == "custom"]
    assert sorted(c.postal for c in custom) == ["ks", "ma", "mn", "nc", "nv", "sc"]
    assert reg["il"].freshness_field == "source_details.agency_reported_date"
    assert reg["pa"].freshness_field == "effective_date"
    assert reg["ct"].freshness_field == "notice_date"
    assert reg["tn"].freshness_field == "notice_date"
    assert reg["wi"].freshness_field == "source_details.agency_received_date"
    assert reg["fl"].freshness_field == "source_details.agency_notification_date"


def test_wi_fl_freshness_uses_typed_agency_dates():
    reg = load_registry()
    for state, field in (("wi", "agency_received_date"),
                         ("fl", "agency_notification_date")):
        row = {"notice_date": None,
               "source_details": json.dumps({field: "2026-09-23"})}
        assert _source_date(row, reg[state].freshness_field) == "2026-09-23"


def test_registry_rejects_unrecognized_freshness_field(tmp_path):
    raw = {"ct": vars(load_registry()["ct"]).copy()}
    del raw["ct"]["postal"]
    raw["ct"]["freshness_field"] = "source_details.unknown_date"
    path = tmp_path / "states.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="bad freshness_field"):
        load_registry(path)


def test_for_run_explicit_states():
    reg = load_registry()
    picked = reg.for_run(states=["CT", "il"])
    assert [c.postal for c in picked] == ["ct", "il"]


def test_for_run_rejects_manual_states():
    reg = load_registry()
    try:
        reg.for_run(states=["ar"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_scheduled_run_only_active():
    reg = load_registry()
    # Nothing is active yet at skeleton stage unless promoted
    for cfg in reg.for_run(cadence="weekly"):
        assert cfg.status == "active"


def test_upstream_states_have_transformers():
    """Every upstream/patched state must resolve a transformer class."""
    from warnlive.normalize.engine import get_transformer_class

    reg = load_registry()
    for cfg in reg.all():
        if cfg.source in ("upstream", "patched"):
            assert get_transformer_class(cfg.postal), cfg.postal
