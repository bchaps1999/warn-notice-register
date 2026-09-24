"""The new agency overlays account for every pinned source row."""

from pathlib import Path

import pytest

from warnlive.migrate import ny_overlay, or_source, tn_source

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data/source_snapshots"


@pytest.mark.parametrize(
    ("project", "directory", "source_rows", "admitted", "held"),
    [
        (or_source.project, "or", 678, 153, 525),
        (tn_source.project, "tn", 143, 140, 3),
        (ny_overlay.project, "ny", 758, 329, 429),
    ],
)
def test_pinned_source_row_accounting(project, directory, source_rows, admitted, held):
    records, exceptions, report = project(SNAPSHOTS / directory)
    assert report == {"source_rows": source_rows, "admitted": admitted, "held": held}
    assert len(records) == len({row["source_identity"] for row in records})
    assert len(records) + len(exceptions) == source_rows
    if directory in {"or", "tn"}:
        assert all(row["notice_date"] is None for row in records)
    else:
        assert all(row["notice_date"] for row in records)


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


def test_ny_display_aliases_do_not_create_two_notices():
    records, held, _ = ny_overlay.project(SNAPSHOTS / "ny")
    hashes = {
        "a9e94675326411fa9aecaee694e931a37c2da542ed4ecd339ac8f79f0d3ecbe9",
        "896067ac393e9024d3b5f71abab93700665f9f459c5122889adb7d3643408850",
    }
    assert hashes <= {item["source_row_sha256"] for item in held}
    assert all(not any(digest in item["source_identity"] for digest in hashes)
               for item in records)


def test_tn_archive_holds_reused_current_notice_number(tmp_path):
    current = tmp_path / "tn.csv"
    current.write_text("Notice ID,Company\n#202400049,S&B Engineers\n")
    records, held, report = tn_source.project(SNAPSHOTS / "tn", current)
    assert report == {"source_rows": 143, "admitted": 139, "held": 4}
    assert any(item["source_notice_id"] == "202400049" and
               item["reason"] == "overlaps_current_source_notice_number" for item in held)
    assert all(item["source_notice_id"] != "202400049" for item in records)
