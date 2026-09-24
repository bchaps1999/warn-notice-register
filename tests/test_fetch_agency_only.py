from pathlib import Path

import pytest

from warnlive.fetch import fetch_state


@pytest.mark.parametrize("postal", ["ga", "ia", "ky", "or", "tn"])
def test_mixed_upstream_collectors_fail_before_fetch(postal, tmp_path, monkeypatch):
    def unexpected_import(_name):
        raise AssertionError("upstream scraper should not be loaded")

    monkeypatch.setattr("warnlive.fetch.import_module", unexpected_import)
    with pytest.raises(ValueError, match="BLN historical append"):
        fetch_state(postal, Path(tmp_path / "raw"), Path(tmp_path / "cache"))
