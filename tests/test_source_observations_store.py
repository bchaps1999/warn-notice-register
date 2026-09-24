"""Source observations remain an auditable input ledger without model results."""

import pytest

from warnlive.store import db
from warnlive.store.observations import store_observations


def _row(pointer="agency/la/2025.pdf:p1:r1"):
    return {
        "source_artifact": "agency/la/2025.pdf",
        "source_row": pointer,
        "source_row_sha256": "row-hash",
        "kind": "notice",
        "company_text": "Example",
        "raw_cells": ["Example"],
    }


def test_observations_record_exclusion_and_replay_without_model_columns(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    row = _row()
    admission = {row["source_row"]: ("event_unresolved", None)}
    first = store_observations(conn, [row], admission, "bundle")
    second = store_observations(conn, [row], admission, "bundle")
    assert first == second
    assert first["by_admission_status"]["event_unresolved"] == 1
    saved = conn.execute("SELECT * FROM source_observations").fetchall()
    assert len(saved) == 1
    assert saved[0]["notice_id"] is None
    assert '"employer":"Example"' in saved[0]["deterministic_json"]
    assert "cleaned_json" not in saved[0].keys()


def test_observation_store_rejects_missing_disposition_atomically(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    with pytest.raises(ValueError, match="match exactly once"):
        store_observations(conn, [_row()], {}, "bundle")
    assert conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0] == 0


def test_observation_store_rolls_back_prior_rows_on_invalid_admission(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    first = _row()
    second = _row("agency/la/2025.pdf:p1:r2")
    with pytest.raises(ValueError, match="only admitted observations"):
        store_observations(conn, [first, second], {
            first["source_row"]: ("event_unresolved", None),
            second["source_row"]: ("admitted", None),
        }, "bundle")
    assert conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0] == 0


def test_observation_store_rejects_changed_source_on_replay(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    first = _row()
    admission = {first["source_row"]: ("event_unresolved", None)}
    store_observations(conn, [first], admission, "bundle")
    changed = {**first, "company_text": "Different"}
    with pytest.raises(ValueError, match="stored source observation changed"):
        store_observations(conn, [changed], admission, "bundle")
    assert conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0] == 1
