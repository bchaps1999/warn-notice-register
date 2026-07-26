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


# --- label matching for CIK-less employers -----------------------------------
#
# No key exists for private companies, so this tier is deliberately narrow:
# only the top unmatched employers by workers affected are looked up, a
# candidate must equal the employer's normalized name on an English label or
# alias, must be an organization-type entity, and must be the ONLY surviving
# candidate. Matches carry method "label" so consumers can hold them to a
# different standard than the key-based CIK joins.

LABELS_PATH = Path("data/reference/wikidata_labels.csv.gz")
API_URL = "https://www.wikidata.org/w/api.php"

# P31 values accepted as "an organization that could file a WARN notice".
_ORG_CLASSES = {
    "Q4830453",  # business
    "Q6881511",  # enterprise
    "Q783794",   # company
    "Q891723",   # public company
    "Q5621421",  # private company... (privately held company)
    "Q167037",   # corporation
    "Q658255",   # subsidiary
    "Q163740",   # nonprofit organization
    "Q16917",    # hospital
    "Q3918",     # university
    "Q23002054", # private not-for-profit educational institution
    "Q507619",   # retail chain
    "Q18558685", # supermarket chain
    "Q46970",    # airline
    "Q22687",    # bank
    "Q187939",   # manufacturer
    "Q1589009",  # privately held company
    "Q43229",    # organization
}


def _api(params: dict) -> dict:
    from time import sleep

    sleep(0.12)
    query = urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(
        f"{API_URL}?{query}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def label_refresh(conn, top_n: int = 1500, out_path: Path = LABELS_PATH) -> int:
    """Resolve the top CIK-less employers to Wikidata via exact-unique
    label matching. Incremental: names already in the reference (matched
    or recorded as misses) are not re-queried."""
    from warnlive.enrich.edgar import REFERENCE_PATH, Matcher
    from warnlive.normalize.engine import normalized_employer

    matcher = Matcher() if REFERENCE_PATH.exists() else None

    # Top unmatched employers by workers affected.
    agg: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT employer_name, substr(COALESCE(notice_date, effective_date),1,4) y, "
        "COALESCE(employees_affected, 0) jobs FROM notices"
    ):
        norm = normalized_employer(r["employer_name"])
        if not norm:
            continue
        if matcher and matcher.match(r["employer_name"], int(r["y"]) if r["y"] else None):
            continue  # CIK tier already covers it
        e = agg.setdefault(norm, {"display": r["employer_name"], "workers": 0})
        e["workers"] += r["jobs"]
    targets = sorted(agg.items(), key=lambda kv: -kv[1]["workers"])[:top_n]

    known: dict[str, dict] = {}
    if out_path.exists():
        with gzip.open(out_path, "rt") as fh:
            for row in csv.DictReader(fh):
                known[row["normalized_name"]] = row

    fetched = 0
    for norm, info in targets:
        if norm in known:
            continue
        fetched += 1
        rec = {"normalized_name": norm, "qid": "", "label": "",
               "parents": "", "industries": ""}
        try:
            hits = _api({"action": "wbsearchentities", "search": info["display"],
                         "language": "en", "type": "item", "limit": 7}
                        ).get("search", [])
            ids = [h["id"] for h in hits]
            survivors = []
            if ids:
                ents = _api({"action": "wbgetentities", "ids": "|".join(ids),
                             "props": "labels|aliases|claims"}).get("entities", {})
                for qid, ent in ents.items():
                    names = [ent.get("labels", {}).get("en", {}).get("value", "")]
                    names += [a["value"] for a in ent.get("aliases", {}).get("en", [])]
                    if norm not in {normalized_employer(x) for x in names if x}:
                        continue
                    claims = ent.get("claims", {})
                    p31 = {
                        c["mainsnak"].get("datavalue", {}).get("value", {}).get("id")
                        for c in claims.get("P31", [])
                        if c.get("mainsnak", {}).get("snaktype") == "value"
                    }
                    if not (p31 & _ORG_CLASSES):
                        continue
                    survivors.append((qid, claims))
            if len(survivors) == 1:
                qid, claims = survivors[0]
                rec["qid"] = qid
                rec["label"] = ents[qid]["labels"]["en"]["value"]
                parent_ids = [
                    c["mainsnak"]["datavalue"]["value"]["id"]
                    for c in claims.get("P749", [])
                    if c.get("mainsnak", {}).get("snaktype") == "value"
                ][:3]
                if parent_ids:
                    pents = _api({"action": "wbgetentities",
                                  "ids": "|".join(parent_ids), "props": "labels"}
                                 ).get("entities", {})
                    rec["parents"] = "||".join(
                        p.get("labels", {}).get("en", {}).get("value", "")
                        for p in pents.values()
                    )
        except Exception as exc:  # noqa: BLE001 — record as miss, retryable later
            logger.warning("wikidata label lookup failed for %r (%s)", norm, exc)
            continue
        known[norm] = rec
        if fetched % 100 == 0:
            logger.info("wikidata labels: %d looked up", fetched)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    matched = sum(1 for r in known.values() if r["qid"])
    with gzip.open(out_path, "wt", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["normalized_name", "qid", "label", "parents", "industries"]
        )
        writer.writeheader()
        for norm in sorted(known):
            writer.writerow(known[norm])
    logger.info("wikidata labels: %d names tried, %d matched -> %s",
                len(known), matched, out_path)
    return matched


def load_labels(path: Path = LABELS_PATH) -> dict[str, dict]:
    """normalized_name -> {qid, label, parents}; only rows that matched."""
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    with gzip.open(path, "rt") as fh:
        for row in csv.DictReader(fh):
            if row["qid"]:
                out[row["normalized_name"]] = row
    return out
