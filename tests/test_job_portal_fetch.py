from importlib import import_module
from pathlib import Path
import json

import pytest

from warnlive.fetch.custom import job_portal


@pytest.mark.parametrize("postal", ["ks", "az", "me", "vt", "de"])
def test_portal_fetch_replaces_raw_only_after_complete_staging(monkeypatch, tmp_path, postal):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / f"{postal}.csv").write_text("old\n")
    seen = []

    def capture(out: Path, portal, **_kwargs):
        seen.append((out, portal.postal))
        out.mkdir(parents=True)
        (out / f"{postal}.csv").write_text("new\n")
        (out / "hold_policy.json").write_text(json.dumps({
            "source": portal.policy_source, "held_ids": [], "held_signatures": [],
        }))
        return {"listed_warn_rows": 2, "detail_pages": 2}

    monkeypatch.setattr(job_portal, "capture", capture)
    monkeypatch.setattr(job_portal, "freeze_capture", lambda *_args: {
        "listed": 2, "staged": 1, "held": 1,
        "raw_sha256": "test-hash", "archive_sha256": "test-archive",
    })
    module = import_module(f"warnlive.fetch.custom.{postal}")
    assert module.scrape(raw, tmp_path / "cache") == raw / f"{postal}.csv"
    assert seen[0][1] == postal and seen[0][0].parent == tmp_path / "cache" / postal
    assert (raw / f"{postal}.csv").read_text() == "new\n"
    policy = json.loads((raw / f"{postal}.hold_policy.json").read_text())
    assert policy["raw_sha256"] == "test-hash"

    monkeypatch.setattr(job_portal, "capture", lambda *_args, **_kwargs: {
        "listed_warn_rows": 2, "detail_pages": 1,
    })
    with pytest.raises(ValueError, match="incomplete"):
        module.scrape(raw, tmp_path / "cache")
    assert (raw / f"{postal}.csv").read_text() == "new\n"
