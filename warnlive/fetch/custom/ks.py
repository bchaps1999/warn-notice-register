"""Complete KANSASWORKS WARN portal capture for scheduled collection."""

from __future__ import annotations

import shutil
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from warnlive.migrate.ks_portal_source import capture, freeze_capture


def scrape(data_dir: Path, cache_dir: Path) -> Path:
    """Fetch every WARN listing and detail before replacing the raw CSV."""
    data_dir, cache_dir = Path(data_dir), Path(cache_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = cache_dir / "ks" / f"portal-{stamp}-{uuid.uuid4().hex[:8]}"
    manifest = capture(evidence, delay=2.0)
    if manifest["listed_warn_rows"] != manifest["detail_pages"]:
        raise ValueError("Kansas portal capture incomplete")
    staged = freeze_capture(evidence, evidence.with_suffix(".tar.gz"))
    if staged["listed"] != staged["staged"] + staged["held"]:
        raise ValueError("Kansas portal dispositions incomplete")
    data_dir.mkdir(parents=True, exist_ok=True)
    current_policy = json.loads((evidence / "hold_policy.json").read_text())
    current_policy["raw_sha256"] = staged["raw_sha256"]
    current_policy["evidence_archive_sha256"] = staged["archive_sha256"]
    policy_out = data_dir / "ks.hold_policy.json"
    policy_tmp = data_dir / "ks.hold_policy.json.tmp"
    policy_tmp.write_text(json.dumps(current_policy, sort_keys=True, indent=2) + "\n")
    policy_tmp.replace(policy_out)
    output = data_dir / "ks.csv"
    temporary = data_dir / "ks.csv.tmp"
    try:
        shutil.copyfile(evidence / "ks.csv", temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
