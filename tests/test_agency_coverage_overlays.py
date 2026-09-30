"""The new agency overlays account for every pinned source row."""

from pathlib import Path

import pytest

from warnlive.migrate import or_source, tn_source

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data/source_snapshots"


@pytest.mark.parametrize(
    ("project", "directory", "source_rows", "admitted", "held"),
    [
        (or_source.project, "or", 678, 200, 340),
        (tn_source.project, "tn", 143, 140, 3),
    ],
)
def test_pinned_source_row_accounting(project, directory, source_rows, admitted, held):
    records, exceptions, report = project(SNAPSHOTS / directory)
    assert {key: report[key] for key in ("source_rows", "admitted", "held")} == {
        "source_rows": source_rows, "admitted": admitted, "held": held}
    # Oregon admits a multi-row WARN number as one notice and a changed live
    # capture as a later version of it; row accounting is by source row.
    notices = len(records) - report.get("capture_versions", 0)
    assert notices == len({row["source_identity"] for row in records})
    assert report.get("admitted_rows", len(records)) + len(exceptions) == source_rows
    assert all(row["notice_date"] is None for row in records)


@pytest.mark.parametrize(("directory", "artifact", "reader"), [
    ("or", "latest.xlsx", or_source.read_artifacts),
    ("or", "live.xlsx", or_source.read_artifacts),
    ("tn", "reports.html", tn_source.read_artifacts),
])
def test_pinned_source_bytes_are_required(tmp_path, directory, artifact, reader):
    source = SNAPSHOTS / directory
    target = tmp_path / directory
    target.mkdir()
    for path in source.iterdir():
        if path.is_file():
            (target / path.name).write_bytes(path.read_bytes())
    content = (source / artifact).read_bytes()
    (target / artifact).write_bytes(content + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        reader(target)


def test_tn_archive_holds_reused_current_notice_number(tmp_path):
    current = tmp_path / "tn.csv"
    current.write_text("Notice ID,Company\n#202400049,S&B Engineers\n")
    records, held, report = tn_source.project(SNAPSHOTS / "tn", current)
    assert report == {"source_rows": 143, "admitted": 139, "held": 4}
    assert any(item["source_notice_id"] == "202400049" and
               item["reason"] == "overlaps_current_source_notice_number" for item in held)
    assert all(item["source_notice_id"] != "202400049" for item in records)
