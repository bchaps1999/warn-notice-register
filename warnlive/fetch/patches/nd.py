"""North Dakota — patched from upstream warn-scraper nd.py.

Job Service ND publishes one cumulative PDF (a print of an Excel sheet).
From 2024 onward the sheet's rows lack the vertical rule between "WARN
Dated" and "Date of Layoff/Closure", so pdfplumber's line-based table
reading merges the two cells: the notice-date cell reads "3/3/2025
5/2/2025" and the layoff-date cell comes back empty, losing the layoff date
(checked 2026-09-29 against the PDF created 2026-02-23).

Rows whose cells all come back ruled are written exactly as upstream writes
them. A row with merged cells is re-read with the table's own column edges
(taken from a fully ruled row of the same table), so each value lands in
the column the agency put it in. Nothing is inferred: the text is the same
characters, only split at the printed column boundary.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pdfplumber

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

PDF_URL = (
    "https://www.jobsnd.com/sites/www/files/documents/jsnd-documents/"
    "WARN%20Notices%202015%20to%20present.pdf"
)
HEADER = "Company Name"


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    cache = Cache(cache_dir)
    pdf_path = cache.download("nd/WARN_Notices_2015_to_present.pdf", PDF_URL)
    output_rows = parse_pdf(pdf_path)
    if not output_rows or output_rows[0][0] != HEADER:
        raise ValueError("ND: WARN PDF header row not found")
    data_path = Path(data_dir) / "nd.csv"
    utils.write_rows_to_csv(data_path, output_rows)
    return data_path


def parse_pdf(pdf_path: Path) -> list[list[str]]:
    """Header plus data rows, with merged cells split at column edges."""
    output_rows: list[list[str]] = []
    header_written = False
    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            for row in _page_rows(page, page_index):
                output_row = [_clean_text(cell) for cell in row]
                if not any(output_row):
                    continue
                if output_row[0] == HEADER:
                    if header_written:
                        continue
                    header_written = True
                output_rows.append(output_row)
    return output_rows


def _page_rows(page, page_index: int) -> list[list[str | None]]:
    tables = page.find_tables()
    if not tables:
        return []
    # pdfplumber's extract_table() reads the table with the most cells.
    table = sorted(tables, key=lambda t: len(t.cells), reverse=True)[0]
    extracted = table.extract()
    width = max((len(r.cells) for r in table.rows), default=0)
    ruled = [
        r for r in table.rows
        if len(r.cells) == width and all(cell is not None for cell in r.cells)
    ]
    edges = (
        [cell[0] for cell in ruled[0].cells] + [ruled[0].cells[-1][2]]
        if ruled else None
    )
    rows: list[list[str | None]] = []
    for table_row, values in zip(table.rows, extracted):
        if edges is None or all(cell is not None for cell in table_row.cells):
            rows.append(values)
            continue
        split = _split_row(page, edges, table_row.bbox)
        if split is None:
            raise ValueError(
                f"ND page {page_index + 1}: merged row could not be re-read "
                f"by column: {values!r}"
            )
        # Re-reading must redistribute the row's text, never add or drop it.
        if _letters(values) != _letters(split):
            raise ValueError(
                f"ND page {page_index + 1}: column re-read changed row text: "
                f"{values!r} -> {split!r}"
            )
        logger.debug("ND page %d: split merged cells %r -> %r", page_index + 1, values, split)
        rows.append(split)
    return rows


def _split_row(page, edges: list[float], bbox) -> list[str | None] | None:
    _x0, top, _x1, bottom = bbox
    region = page.crop((edges[0], top, edges[-1], bottom))
    rows = region.extract_table({
        "vertical_strategy": "explicit",
        "explicit_vertical_lines": edges,
        "horizontal_strategy": "explicit",
        "explicit_horizontal_lines": [top, bottom],
    })
    if not rows or len(rows) != 1 or len(rows[0]) != len(edges) - 1:
        return None
    return rows[0]


def _letters(cells) -> str:
    return "".join(sorted(re.sub(r"\s+", "", "".join(c or "" for c in cells))))


def _clean_text(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text).strip()
