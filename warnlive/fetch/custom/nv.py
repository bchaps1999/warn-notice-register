"""Nevada — custom adapter (not covered by warn-scraper).

NV DETR publishes one master PDF per year ("YYYY WARN Act Notices" links on
detr.nv.gov/Page/WARN) mixing WARN and Non-WARN actions. The PDFs have a
ruled header box but unruled data rows whose cell texts physically abut, so
default pdfplumber table extraction fails. We derive column x-boundaries
from the detected header table, then bucket individual characters below the
header into those columns, grouping lines by vertical position.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

PAGE_URL = "https://detr.nv.gov/Page/WARN"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
COLUMNS = [
    "Received Date",
    "Effective Date",
    "Type",
    "Affected Total",
    "Employer",
    "City",
    "County",
    "Notification",
]
LINE_TOLERANCE = 3  # points; chars within this vertical distance share a row


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    cache = Cache(cache_dir)
    r = requests.get(PAGE_URL, headers=HEADERS, timeout=120)
    r.raise_for_status()
    cache.write("nv/index.html", r.text)

    soup = BeautifulSoup(r.text, "html5lib")
    pdf_links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text().split())
        if re.search(r"\bWARN Act Notices\b", text, re.I):
            href = requests.compat.urljoin(PAGE_URL, a["href"])
            pdf_links.append((text, href))
    if not pdf_links:
        raise ValueError("NV: no 'WARN Act Notices' PDF links found")

    rows: list[dict] = []
    for text, href in pdf_links:
        logger.debug("NV fetching %s (%s)", href, text)
        r = requests.get(href, headers=HEADERS, timeout=120)
        r.raise_for_status()
        name = f"nv/{href.rsplit('/', 1)[-1]}"
        pdf_path = cache.write_binary(name, r.content)
        rows.extend(parse_pdf(pdf_path))

    if not rows:
        raise ValueError("NV: parsed zero rows from PDFs")

    data_path = data_dir / "nv.csv"
    with open(data_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return data_path


def parse_pdf(path: Path) -> list[dict]:
    rows: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = page.find_tables()
            if not tables:
                continue
            header = tables[0]
            # Column boundaries: unique x-edges of the ruled header cells.
            edges = sorted({round(x, 1) for cell in header.cells for x in (cell[0], cell[2])})
            if len(edges) != len(COLUMNS) + 1:
                logger.warning(
                    "NV %s: header has %d column edges, expected %d — skipping page",
                    path.name, len(edges), len(COLUMNS) + 1,
                )
                continue
            bottom = header.bbox[3]

            # Group chars below the header into lines, then bucket by column.
            lines: dict[float, list] = {}
            for ch in page.chars:
                if ch["top"] <= bottom or ch["x0"] < edges[0] - 2 or ch["x0"] >= edges[-1] + 2:
                    continue
                key = next(
                    (k for k in lines if abs(k - ch["top"]) <= LINE_TOLERANCE), None
                )
                lines.setdefault(key if key is not None else ch["top"], []).append(ch)

            for top in sorted(lines):
                cells = [""] * len(COLUMNS)
                last_x1 = [0.0] * len(COLUMNS)
                for ch in sorted(lines[top], key=lambda c: c["x0"]):
                    idx = max(
                        i for i in range(len(COLUMNS)) if ch["x0"] >= edges[i] - 2
                    )
                    idx = min(idx, len(COLUMNS) - 1)
                    if cells[idx] and ch["x0"] - last_x1[idx] > 1:
                        cells[idx] += " "
                    cells[idx] += ch["text"]
                    last_x1[idx] = ch["x1"]
                cells = [c.strip() for c in cells]
                row = dict(zip(COLUMNS, cells))
                # Keep only real data rows; drops sidebar/footnote text.
                if row["Notification"].lower().replace("-", "") in ("warn", "nonwarn") and (
                    row["Employer"] or row["Received Date"]
                ):
                    rows.append(row)
    return rows
