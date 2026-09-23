"""The directly published Louisiana PDFs are frozen evidence, not yet normalized rows."""

import hashlib
import json
from pathlib import Path

import pdfplumber


ROOT = Path(__file__).resolve().parents[1] / "data/source_snapshots/la"


def test_louisiana_pdf_artifacts_match_manifest():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert manifest["format"] == "warn-source-artifacts-v1"
    for item in manifest["artifacts"]:
        path = ROOT / item["path"]
        assert path.stat().st_size == item["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        with pdfplumber.open(path) as pdf:
            assert len(pdf.pages) == item["pages"]


def test_louisiana_pdf_layout_preserves_ranges_sites_and_rescission():
    with pdfplumber.open(ROOT / "2025.pdf") as pdf:
        rows = [row for page in pdf.pages for row in page.extract_table()[1:]]
    assert len(rows) == 25
    assert any("7/31/25\nto\n12/31/25" in (row[2] or "") for row in rows)
    assert any("Rescinded on 9/5/2025" in (row[0] or "") for row in rows)
    assert any("1500 N. Airway Drive" in (row[0] or "") for row in rows)

    with pdfplumber.open(ROOT / "2026.pdf") as pdf:
        rows = pdf.pages[0].extract_table()[1:]
    assert len(rows) == 14
    mosaic = next(row for row in rows if "Mosaic Company" in (row[0] or ""))
    assert "Convent" in mosaic[1] and "St. James" in mosaic[1]
    assert mosaic[4] == "206"  # a total, not an allocation to either site
    westlake = next(row for row in rows if "Westlake" in (row[0] or ""))
    assert westlake[2] == "12/15/25"  # notice year differs from report year
