"""Make a deterministic, evidence-only Iowa official-row review worklist.

The worklist compares *observations*, not WARN events. An exact or unique
correspondence is never authorization to merge, ingest, or add worker counts.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from warnlive.normalize.engine import _fold


KNOWN_NOTICE_TYPES = {
    "amendement - change in number",  # repeated agency label for the 14 CNH phases
    "amendment", "amendment - additional employees",
    "amendment - change in date",
    "amendment - reduction in number laid off",
    "amendment- change in date/reduction in number laid off",
    "closing", "closure", "mass layoff",
    "mass layoff - additional employees",
}
COMPARE_FIELDS = (
    "company_text", "street_address_text", "city_text", "county_text",
    "address_state_text", "postal_code_text", "notice_type_text",
    "workers_reported", "notice_date", "effective_date",
    "local_workforce_area_text", "industry_text",
)


def _identity(row: dict) -> tuple[str, str]:
    return _fold(row.get("company_text")), _fold(row.get("city_text"))


def _facts(row: dict) -> tuple:
    return (*_identity(row), row.get("notice_date"),
            row.get("effective_date"), row.get("workers_reported"))


def _pointer(row: dict) -> dict:
    return {"source_row": row["source_row"],
            "source_row_sha256": row["source_row_sha256"]}


def _summary(row: dict) -> dict:
    return {
        **_pointer(row), "source_artifact": row["source_artifact"],
        "company": row.get("company_text"), "city": row.get("city_text"),
        "notice_date": row.get("notice_date"),
        "effective_date": row.get("effective_date"),
        "workers": row.get("workers_reported"),
        "notice_type": row.get("notice_type_text"),
        "address_state": row.get("address_state_text"),
    }


def _sort_row(row: dict) -> tuple:
    return (row.get("notice_date") is None, row.get("notice_date") or "",
            row.get("effective_date") or "", row["source_row"])


def _changed_fields(left: dict, right: dict) -> list[str]:
    def comparable(row: dict, field: str) -> object:
        value = row.get(field)
        if field == "postal_code_text" and value is not None:
            postal = str(value).strip()
            return postal.zfill(5) if postal.isdigit() and len(postal) <= 5 else postal
        return value

    return [field for field in COMPARE_FIELDS
            if comparable(left, field) != comparable(right, field)]


def _needs_base_review(row: dict) -> bool:
    notice_type = str(row.get("notice_type_text") or "").casefold()
    return "amend" in notice_type or "additional employees" in notice_type


def _anomalies(rows: list[dict]) -> list[dict]:
    duplicate_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        duplicate_index[(row["source_artifact"], row["source_row_sha256"])].append(row)
    result = []
    for row in rows:
        flags = []
        if not row.get("notice_date") or not row.get("effective_date"):
            flags.append("malformed_date")
        if not str(row.get("city_text") or "").strip():
            flags.append("missing_city")
        if str(row.get("address_state_text") or "").strip().upper() != "IA":
            flags.append("non_ia_address_state")
        if len(duplicate_index[(row["source_artifact"], row["source_row_sha256"])]) > 1:
            flags.append("exact_duplicate_row")
        notice_type = str(row.get("notice_type_text") or "")
        if notice_type.strip().casefold() not in KNOWN_NOTICE_TYPES:
            flags.append("unrecognized_notice_type")
        flags.extend(f"source_layout:{issue}" for issue in row.get("layout_issues", []))
        if flags:
            result.append({**_pointer(row), "flags": sorted(set(flags))})
    return result


def build(report: dict) -> dict:
    """Project an ia_reconcile report without making event-identity decisions."""
    if report.get("format") != "warn-ia-correspondence-v1":
        raise ValueError("expected warn-ia-correspondence-v1 input")
    if "official" not in report or "historical_official" not in report:
        raise ValueError("Iowa worklist requires both frozen official source logs")
    current = [item["official"] for item in report["official"]["rows"]]
    historical = [item["official"] for item in report["historical_official"]["rows"]]
    all_rows = sorted(current + historical, key=lambda row: row["source_row"])
    pointers = [row["source_row"] for row in all_rows]
    if len(pointers) != len(set(pointers)):
        raise ValueError("duplicate Iowa source pointer")
    indexes = []
    for opposite in (current, historical):
        index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in opposite:
            index[_identity(row)].append(row)
        indexes.append(index)

    cross_source = []
    counts: Counter[str] = Counter()
    for source_rows, own, opposite in ((current, indexes[0], indexes[1]),
                                       (historical, indexes[1], indexes[0])):
        for row in source_rows:
            same_identity = sorted(opposite.get(_identity(row), []), key=_sort_row)
            exact = [candidate for candidate in same_identity
                     if _facts(candidate) == _facts(row)]
            candidates = exact or same_identity
            same_side_exact = [candidate for candidate in own[_identity(row)]
                               if _facts(candidate) == _facts(row)]
            if len(candidates) > 1 or (exact and len(same_side_exact) > 1) or (
                not exact and candidates and len(own[_identity(row)]) > 1
            ):
                bucket = "ambiguous"
            elif len(candidates) == 1:
                if not exact:
                    bucket = "weak_unique"
                else:
                    changed = _changed_fields(row, candidates[0])
                    bucket = "changed_fields" if changed else "strong_unique"
            else:
                bucket = "no_cross_source_counterpart"
            counts[bucket] += 1
            cross_source.append({
                **_pointer(row), "bucket": bucket,
                "match_basis": "same_facts_and_place" if exact else
                               "same_employer_and_city" if candidates else None,
                "candidates": [{**_pointer(candidate),
                                "changed_fields": _changed_fields(row, candidate)}
                               for candidate in candidates],
            })
    cross_source.sort(key=lambda item: item["source_row"])

    by_identity: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in all_rows:
        by_identity[_identity(row)].append(row)
    histories = []
    for identity, rows in sorted(by_identity.items()):
        if len(rows) < 2 and not _needs_base_review(rows[0]):
            continue
        histories.append({
            "employer_key": identity[0], "city_key": identity[1],
            "rows": [_summary(row) for row in sorted(rows, key=_sort_row)],
        })
    anomalies = _anomalies(all_rows)
    return {
        "format": "warn-ia-review-worklist-v1",
        "decision_policy": "evidence_only_no_automatic_event_identity_or_ingest",
        "inputs": report.get("inputs", {}),
        "counts": {
            "official_current_rows": len(current),
            "official_historical_rows": len(historical),
            "cross_source_buckets": dict(sorted(counts.items())),
            "history_groups": len(histories),
            "anomaly_rows": len(anomalies),
        },
        "cross_source": cross_source,
        "amendment_histories": histories,
        "anomalies": anomalies,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    worklist = build(json.loads(args.report.read_text()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as fh:
        fh.write(json.dumps(worklist, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(json.dumps(worklist["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
