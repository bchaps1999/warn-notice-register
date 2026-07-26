"""Legal Entity Identifiers from GLEIF, for employers with no CIK or EIN.

Large private employers — the Delaware Norths and MV Transportations of
the data — are neither SEC registrants nor exempt organizations, so
neither identity tier reaches them. Many do hold a Legal Entity
Identifier, the ISO 17442 code banks and regulators use, published by
GLEIF under a public-domain licence with a free API.

An LEI carries no industry data; what it adds is a durable identifier and
the entity's registered legal name, which is often the only way to tell
that two spellings on two states' notices are the same company.

Name matching is gated as elsewhere — exact equality after normalization,
US jurisdiction, and a single survivor. Registered addresses are mostly
incorporation states (Delaware), so unlike the nonprofit tier there is no
geography gate to lean on; uniqueness does the work instead.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from time import sleep

logger = logging.getLogger("warnlive")

API_URL = "https://api.gleif.org/api/v1/lei-records"
RECORD_URL = "https://search.gleif.org/#/record/{lei}"
USER_AGENT = (
    "warn-notice-register/1.0 (https://github.com/bchaps1999/warn-notice-register)"
)
PATH = Path("data/reference/gleif.csv.gz")
FIELDS = ["normalized_name", "lei", "legal_name", "jurisdiction", "status"]


def load(path: Path = PATH) -> dict[str, dict]:
    """normalized_name -> matched entity (misses omitted)."""
    if not path.exists():
        return {}
    with gzip.open(path, "rt") as fh:
        return {r["normalized_name"]: r for r in csv.DictReader(fh) if r["lei"]}


def _search(name: str) -> list[dict]:
    sleep(0.6)  # pace a free, unauthenticated public API
    query = urllib.parse.urlencode(
        {"filter[entity.legalName]": name, "page[size]": 10}
    )
    req = urllib.request.Request(f"{API_URL}?{query}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read()).get("data", [])
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 400):  # unmatched or unqueryable name
            return []
        raise


def refresh(conn, top_n: int = 3000, out_path: Path = PATH) -> int:
    """Resolve the largest employers with no CIK to LEIs. Incremental:
    names already recorded — matched or missed — are not re-queried."""
    from warnlive.enrich.edgar import REFERENCE_PATH, Matcher
    from warnlive.normalize.engine import normalized_employer

    matcher = Matcher() if REFERENCE_PATH.exists() else None

    agg: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT employer_name, substr(COALESCE(notice_date, effective_date), 1, 4) AS y, "
        "       COALESCE(employees_affected, 0) AS jobs FROM notices"
    ):
        norm = normalized_employer(r["employer_name"])
        if not norm:
            continue
        if matcher and matcher.match(r["employer_name"], int(r["y"]) if r["y"] else None):
            continue
        e = agg.setdefault(norm, {"display": r["employer_name"], "workers": 0})
        e["workers"] += r["jobs"]
    targets = sorted(agg.items(), key=lambda kv: -kv[1]["workers"])[:top_n]

    known: dict[str, dict] = {}
    if out_path.exists():
        with gzip.open(out_path, "rt") as fh:
            known = {r["normalized_name"]: r for r in csv.DictReader(fh)}

    looked_up = 0
    for norm, info in targets:
        if norm in known:
            continue
        looked_up += 1
        rec = dict.fromkeys(FIELDS, "")
        rec["normalized_name"] = norm
        try:
            survivors = {}
            for record in _search(info["display"]):
                entity = record.get("attributes", {}).get("entity", {})
                legal = (entity.get("legalName") or {}).get("name") or ""
                jurisdiction = entity.get("jurisdiction") or ""
                if normalized_employer(legal) != norm or not jurisdiction.startswith("US"):
                    continue
                survivors[record["id"]] = {
                    "legal_name": legal,
                    "jurisdiction": jurisdiction,
                    "status": entity.get("status") or "",
                }
        except Exception as exc:  # noqa: BLE001 — recorded as a miss, retryable
            logger.warning("GLEIF lookup failed for %r (%s)", norm, exc)
            continue
        if len(survivors) == 1:
            lei, found = next(iter(survivors.items()))
            rec.update(lei=lei, **found)
        known[norm] = rec
        if looked_up % 200 == 0:
            logger.info("GLEIF: %d looked up", looked_up)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for norm in sorted(known):
            writer.writerow(known[norm])
    matched = sum(1 for r in known.values() if r["lei"])
    logger.info("GLEIF: %d matched of %d names probed", matched, len(known))
    return matched
