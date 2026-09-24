"""Conservative, source-specific admission rules shared by live and rebuild runs."""

from __future__ import annotations

from collections import defaultdict


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
