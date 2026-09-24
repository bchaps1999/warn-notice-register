from pathlib import Path
import json

import pytest

from warnlive.fetch.custom import ks


def test_kansas_fetch_replaces_raw_only_after_complete_staging(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "ks.csv").write_text("old\n")

    def capture(out: Path, **_kwargs):
        out.mkdir(parents=True)
        (out / "ks.csv").write_text("new\n")
        (out / "hold_policy.json").write_text(json.dumps({
            "source": "kansasworks_warn_portal", "held_ids": [],
            "held_signatures": [],
        }))
        return {"listed_warn_rows": 2, "detail_pages": 2}

    monkeypatch.setattr(ks, "capture", capture)
    monkeypatch.setattr(ks, "freeze_capture", lambda *_args: {
        "listed": 2, "staged": 1, "held": 1,
        "raw_sha256": "test-hash", "archive_sha256": "test-archive",
    })
    assert ks.scrape(raw, tmp_path / "cache") == raw / "ks.csv"
    assert (raw / "ks.csv").read_text() == "new\n"
    assert json.loads((raw / "ks.hold_policy.json").read_text())["raw_sha256"] == "test-hash"

    monkeypatch.setattr(ks, "capture", lambda *_args, **_kwargs: {
        "listed_warn_rows": 2, "detail_pages": 1,
    })
    with pytest.raises(ValueError, match="incomplete"):
        ks.scrape(raw, tmp_path / "cache")
    assert (raw / "ks.csv").read_text() == "new\n"
