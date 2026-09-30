"""North Dakota's PDF drops a column rule on newer rows; the patch splits them."""

from pathlib import Path

import pytest

from warnlive.fetch.patches import nd

# Column edges and row bands (PDF points from the page top).
EDGES = [50, 175, 285, 340, 440, 540]
HEADER = ["Company Name", "Location", "WARN Dated", "Date of Layoff", "Number"]


def _pdf(path: Path, rows: list[tuple[list[str], bool]]) -> Path:
    """Write a one-page ruled-table PDF. Each row is (cells, ruled): an
    unruled row omits the vertical rule between columns 3 and 4, as the
    agency's 2024+ rows do."""
    height = 60 + 30 * (len(rows) + 1)
    ops = ["0.5 w"]
    all_rows = [(HEADER, True)] + rows
    for i, (cells, ruled) in enumerate(all_rows):
        top = 40 + 30 * i
        bottom = top + 30
        y_top, y_bottom = height - top, height - bottom
        for y in (y_top, y_bottom):
            ops.append(f"{EDGES[0]} {y} m {EDGES[-1]} {y} l S")
        for j, x in enumerate(EDGES):
            if j == 3 and not ruled:
                continue
            ops.append(f"{x} {y_top} m {x} {y_bottom} l S")
        for j, text in enumerate(cells):
            ops.append(
                f"BT /F1 9 Tf {EDGES[j] + 3} {y_bottom + 10} Td ({text}) Tj ET"
            )
    stream = "\n".join(ops).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 {height}] "
         "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>").encode(),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    path.write_bytes(bytes(out))
    return path


def test_merged_date_cells_split_at_the_printed_column_edge(tmp_path):
    pdf = _pdf(tmp_path / "nd.pdf", [
        (["Old Co", "Fargo, ND", "7/31/2015", "12/31/2015", "95"], True),
        (["New Co", "Statewide", "1/15/2026", "1/28/2026", "47"], False),
    ])
    rows = nd.parse_pdf(pdf)
    assert rows == [
        HEADER,
        ["Old Co", "Fargo, ND", "7/31/2015", "12/31/2015", "95"],
        ["New Co", "Statewide", "1/15/2026", "1/28/2026", "47"],
    ]


def test_ruled_rows_match_upstream_reading(tmp_path):
    import pdfplumber

    pdf = _pdf(tmp_path / "nd.pdf", [
        (["Old Co", "Fargo, ND", "7/31/2015", "12/31/2015", "95"], True),
    ])
    with pdfplumber.open(pdf) as doc:
        upstream = [[nd._clean_text(c) for c in row] for row in doc.pages[0].extract_table()]
    assert nd.parse_pdf(pdf) == upstream


def test_scrape_rejects_a_pdf_without_the_header(tmp_path, monkeypatch):
    pdf = tmp_path / "empty.pdf"
    _pdf(pdf, [])

    class Cache:
        def __init__(self, _dir):
            pass

        def download(self, _key, _url):
            return pdf

    monkeypatch.setattr(nd, "Cache", Cache)
    monkeypatch.setattr(nd, "parse_pdf", lambda _path: [])
    with pytest.raises(ValueError, match="header"):
        nd.scrape(tmp_path / "raw", tmp_path / "cache")
