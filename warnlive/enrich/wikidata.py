"""Wikidata enrichment, keyed on SEC CIK (exact joins only).

Wikidata stores the SEC CIK as property P5531, so every CIK-matched
notice can be joined to its Wikidata entity without name matching. One
bulk SPARQL query fetches all P5531-bearing entities with their label,
parent company (P749), and industry labels (P452); `wikidata-refresh`
distills them into data/reference/wikidata_orgs.csv.gz. Export/site read
only the distilled file.

Fuzzy name matching against Wikidata is deliberately NOT done — without
a key it imports wrong entities silently. CIK-less employers stay
unenriched.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger("warnlive")

WDQS_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "warn-notice-register/1.0 (https://github.com/bchaps1999/warn-notice-register)"
ORGS_PATH = Path("data/reference/wikidata_orgs.csv.gz")

# All entities with a SEC CIK; labels resolved server-side, industries and
# parents concatenated so each entity is one row.
SPARQL = """
SELECT ?item ?cik ?itemLabel
       (GROUP_CONCAT(DISTINCT ?parentLabel; separator="||") AS ?parents)
       (GROUP_CONCAT(DISTINCT ?industryLabel; separator="||") AS ?industries)
WHERE {
  ?item wdt:P5531 ?cik .
  OPTIONAL { ?item wdt:P749 ?parent .
             ?parent rdfs:label ?parentLabel FILTER(LANG(?parentLabel) = "en") }
  OPTIONAL { ?item wdt:P452 ?industry .
             ?industry rdfs:label ?industryLabel FILTER(LANG(?industryLabel) = "en") }
  ?item rdfs:label ?itemLabel FILTER(LANG(?itemLabel) = "en")
}
GROUP BY ?item ?cik ?itemLabel
"""


def refresh(out_path: Path = ORGS_PATH) -> int:
    query = urllib.parse.urlencode({"query": SPARQL, "format": "json"})
    req = urllib.request.Request(
        f"{WDQS_URL}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())

    rows = []
    for b in data["results"]["bindings"]:
        cik = b["cik"]["value"].strip()
        if not cik.isdigit():
            continue
        rows.append(
            [
                int(cik),
                b["item"]["value"].rsplit("/", 1)[-1],  # QID
                b["itemLabel"]["value"],
                b.get("parents", {}).get("value", ""),
                b.get("industries", {}).get("value", ""),
            ]
        )
    # One row per CIK: prefer the entity with a parent, then more industries
    # (duplicates exist where predecessor/successor entities share a CIK).
    best: dict[int, list] = {}
    for r in sorted(rows, key=lambda r: (bool(r[3]), len(r[4])), reverse=True):
        best.setdefault(r[0], r)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["cik", "qid", "label", "parents", "industries"])
        for cik in sorted(best):
            writer.writerow(best[cik])
    logger.info("Wikidata orgs: %d CIK-keyed entities -> %s", len(best), out_path)
    return len(best)


def load_orgs(path: Path = ORGS_PATH) -> dict[int, dict]:
    """cik -> {qid, label, parents, industries}; {} when absent."""
    if not path.exists():
        return {}
    out: dict[int, dict] = {}
    with gzip.open(path, "rt") as fh:
        for row in csv.DictReader(fh):
            out[int(row["cik"])] = row
    return out
