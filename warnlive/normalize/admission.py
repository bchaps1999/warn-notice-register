"""Conservative, source-specific admission rules shared by live and rebuild runs."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re


KS_HOLDS_PATH = Path(__file__).with_name("ks_portal_holds.json")


def ks_event_signature(employer: str | None, notice_date: str | None) -> tuple[str, str] | None:
    """A conservative apparent-event key; it is never a filing identity."""
    if not employer or not notice_date:
        return None
    return (re.sub(r"[^a-z0-9]+", "", employer.casefold()), notice_date.strip().casefold())


def ks_ambiguity_reasons(records: list[dict], existing: list[dict],
                         held_ids: set[str],
                         held_signatures: set[tuple[str, str]] | None = None) -> dict[str, str]:
    """Hold reviewed portal IDs and later same-employer/day arrivals."""
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in [*records, *existing]:
        signature = ks_event_signature(row.get("employer_name"), row.get("notice_date"))
        identity = row.get("source_identity")
        if signature and identity:
            grouped[signature].add(identity)
    reasons = {}
    for row in records:
        identity = row.get("source_identity")
        signature = ks_event_signature(row.get("employer_name"), row.get("notice_date"))
        if identity in held_ids or signature in (held_signatures or set()):
            reasons[row["dedupe_key"]] = "reviewed_portal_event_identity_unresolved"
        elif signature and len(grouped[signature]) > 1:
            reasons[row["dedupe_key"]] = "same_employer_day_event_identity_unresolved"
    return reasons


def load_ks_hold_policy(path: Path = KS_HOLDS_PATH) -> tuple[set[str], set[tuple[str, str]]]:
    payload = json.loads(path.read_text())
    if payload.get("source") != "kansasworks_warn_portal" or not isinstance(payload.get("held_ids"), list):
        raise ValueError("invalid Kansas portal hold policy")
    ids = payload["held_ids"]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r"KS:\d+", value) for value in ids):
        raise ValueError("invalid Kansas portal held IDs")
    signatures = payload.get("held_signatures")
    if not isinstance(signatures, list) or any(
        not isinstance(item, list) or len(item) != 2
        or not all(isinstance(part, str) and part for part in item)
        for item in signatures
    ):
        raise ValueError("invalid Kansas portal held signatures")
    return set(ids), {tuple(item) for item in signatures}


def persist_ks_hold_policy(current_path: Path,
                           durable_path: Path) -> tuple[set[str], set[tuple[str, str]]]:
    """Keep all reviewed and newly discovered holds in repository data."""
    baseline_ids, baseline_signatures = load_ks_hold_policy()
    prior_ids, prior_signatures = (
        load_ks_hold_policy(durable_path) if durable_path.exists() else (set(), set())
    )
    current_ids, current_signatures = load_ks_hold_policy(current_path)
    ids = baseline_ids | prior_ids | current_ids
    signatures = baseline_signatures | prior_signatures | current_signatures
    if ids != prior_ids or signatures != prior_signatures or not durable_path.exists():
        previous = json.loads(durable_path.read_text()) if durable_path.exists() else {}
        current = json.loads(current_path.read_text())
        sources = list(previous.get("evidence_archives", []))
        archive = current.get("evidence_archive_sha256")
        if archive and archive not in sources:
            sources.append(archive)
        payload = {
            "source": "kansasworks_warn_portal",
            "held_ids": sorted(ids, key=lambda value: int(value[3:])),
            "held_signatures": [list(item) for item in sorted(signatures)],
            "evidence_archives": sorted(sources),
        }
        durable_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = durable_path.with_suffix(durable_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
        temporary.replace(durable_path)
    return ids, signatures


def exclusion_reasons(postal: str, records: list[dict]) -> dict[str, str]:
    """Return a terminal reason for every excluded event key.

    An entire key group is excluded so a conflicting row cannot silently
    change which observation supplies the canonical notice.
    """
    postal = postal.lower()
    if postal not in {"ga", "ia", "il", "ks", "nj"}:
        return {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[record["dedupe_key"]].append(record)
    excluded: dict[str, str] = {}
    for key, group in grouped.items():
        missing_id = any(not row.get("source_identity") for row in group)
        differing = len({row["raw_record_hash"] for row in group}) > 1
        if postal in {"il", "ks", "nj"} and missing_id:
            excluded[key] = "missing_source_identity"
        elif postal == "nj" and len(group) > 1:
            excluded[key] = "duplicate_source_observation"
        elif postal == "ia" and differing:
            excluded[key] = "conflicting_same_key"
        elif postal == "ga" and missing_id and differing:
            excluded[key] = "conflicting_same_key"
        elif postal in {"il", "ks"} and differing:
            excluded[key] = "conflicting_same_key"
    return excluded
