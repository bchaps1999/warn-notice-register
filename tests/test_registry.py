from warnlive.registry import load_registry


def test_registry_loads_and_validates():
    reg = load_registry()
    assert len(reg.all()) == 52  # 50 states + DC + PR
    manual = [c for c in reg.all() if c.source == "manual"]
    assert sorted(c.postal for c in manual) == ["ar", "nh", "pr", "wv", "wy"]
    custom = [c for c in reg.all() if c.source == "custom"]
    assert sorted(c.postal for c in custom) == ["ma", "mn", "nc", "nv"]


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
