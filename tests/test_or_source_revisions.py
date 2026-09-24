from warnlive.migrate import or_source


def test_identical_cached_copies_do_not_hide_changed_live_capture(monkeypatch, tmp_path):
    original = {"WARN#": 123, "Company Name": "Example", "Location": "Portland",
                "Layoff Date": "2020-01-01", "Laid Off": 127,
                "Layoff Type": "Closure", "Received Date": "2019-11-01"}
    updated = {**original, "Laid Off": 2}
    rows = [
        {"source_row": 4, "source_artifact": "agency/or/latest.xlsx", "snapshot": "cached",
         "source_sha256": "cached", "raw": original, "source_row_sha256": "row4"},
        {"source_row": 5, "source_artifact": "agency/or/latest.xlsx", "snapshot": "cached",
         "source_sha256": "cached", "raw": original, "source_row_sha256": "row5"},
        {"source_row": 4, "source_artifact": "agency/or/live.xlsx", "snapshot": "live",
         "source_sha256": "live", "raw": updated, "source_row_sha256": "live4"},
    ]
    monkeypatch.setattr(or_source, "read_artifacts", lambda _: (rows, {"source_url": "https://example.org"}))
    records, held, report = or_source.project(tmp_path)
    assert records == []
    assert len(held) == report["held"] == 3
    assert {row["reason"] for row in held[:2]} == {"changed_between_agency_captures"}
    assert all(row["disposition"] != "notice" for row in held)
