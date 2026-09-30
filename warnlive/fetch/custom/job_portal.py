"""Complete job-portal WARN capture for scheduled collection (KS, AZ, ME, VT, DE).

``scrape_portal`` fetches every WARN-filtered listing page and every listed
detail page of one state's portal (``migrate.job_portal_source``; network
access at a 2 s pace), freezes the pages into an evidence archive under
``cache_dir/<postal>/``, and only then replaces ``data_dir/<postal>.csv`` with
the staged rows and ``data_dir/<postal>.hold_policy.json`` with the capture's
holds. Rows held at staging (same employer and notice day, name variants,
missing notice date, listing/detail disagreement) are absent from the CSV.

The live pipeline (``pipeline._run_one``) reads that hold policy for every
portal: it excludes any CSV row whose record ID or employer/day matches a held
row and lists the held record IDs under ``admission.staging_held_ids`` in the
run report. The offline rebuild's portal projection
(``migrate.job_portal_source.project``) has two further holds with no live
counterpart: ``record_in_earlier_capture`` (a record already read from the
bundle's earlier ``raw/<postal>.csv``) and ``same_key_as_admitted_notice``. A
live run reads one current capture and ingests a row whose key is already
stored as that notice's next version, so neither situation arises as a hold;
``same_key_as_admitted_notice`` held 0 rows in the 2026-09-30 recovery replay.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from warnlive.migrate.job_portal_source import PORTALS, capture, freeze_capture


def scrape_portal(postal: str, data_dir: Path, cache_dir: Path) -> Path:
    """Fetch every WARN listing and detail before replacing the raw CSV."""
    portal = PORTALS[postal]
    data_dir, cache_dir = Path(data_dir), Path(cache_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = cache_dir / postal / f"portal-{stamp}-{uuid.uuid4().hex[:8]}"
    manifest = capture(evidence, portal, delay=2.0)
    if manifest["listed_warn_rows"] != manifest["detail_pages"]:
        raise ValueError(f"{portal.name} portal capture incomplete")
    staged = freeze_capture(evidence, evidence.with_suffix(".tar.gz"), portal)
    if staged["listed"] != staged["staged"] + staged["held"]:
        raise ValueError(f"{portal.name} portal dispositions incomplete")
    data_dir.mkdir(parents=True, exist_ok=True)
    current_policy = json.loads((evidence / "hold_policy.json").read_text())
    current_policy["raw_sha256"] = staged["raw_sha256"]
    current_policy["evidence_archive_sha256"] = staged["archive_sha256"]
    policy_out = data_dir / f"{postal}.hold_policy.json"
    policy_tmp = data_dir / f"{postal}.hold_policy.json.tmp"
    policy_tmp.write_text(json.dumps(current_policy, sort_keys=True, indent=2) + "\n")
    policy_tmp.replace(policy_out)
    output = data_dir / portal.raw_name
    temporary = data_dir / f"{portal.raw_name}.tmp"
    try:
        shutil.copyfile(evidence / portal.raw_name, temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
