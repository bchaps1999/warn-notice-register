"""Build one-to-one employer URL aliases from two notice exports.

An entity-reference change may move notices between employer_key groups.
Only a key absent from the new export with one unambiguous destination can
be an automatic alias; surviving or split keys need review.
"""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from collections import defaultdict
from pathlib import Path

FIELDS = ["old_key", "new_key"]


def _keys(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            notice, employer = row["dedupe_key"], row["employer_key"]
            if notice in result:
                raise ValueError(f"duplicate notice key in {path}: {notice}")
            result[notice] = employer
    return result


def build(old_csv: Path, new_csv: Path, out_csv: Path) -> dict[str, int]:
    old, new = _keys(old_csv), _keys(new_csv)
    if old.keys() != new.keys():
        raise ValueError("notice key sets differ; reconcile notices before URL aliases")
    destinations: dict[str, set[str]] = defaultdict(set)
    for notice, old_key in old.items():
        if old_key != new[notice]:
            destinations[old_key].add(new[notice])
    live = set(new.values())
    proposed = {
        old_key: next(iter(targets))
        for old_key, targets in destinations.items()
        if old_key not in live and len(targets) == 1
    }
    existing: dict[str, str] = {}
    if out_csv.exists():
        with out_csv.open(newline="") as fh:
            for row in csv.DictReader(fh):
                old_key, target = row["old_key"], row["new_key"]
                if old_key in existing and existing[old_key] != target:
                    raise ValueError(f"conflicting existing redirect for {old_key}")
                existing[old_key] = target
    for old_key, target in proposed.items():
        if old_key in existing and existing[old_key] != target:
            raise ValueError(f"redirect changed for {old_key}; review manually")
        existing[old_key] = target
    # Never shadow a current employer page or point at a missing group.
    for old_key, target in existing.items():
        if old_key in live or target not in live:
            raise ValueError(f"stale or shadowing redirect: {old_key} -> {target}")

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=out_csv.parent, prefix=f".{out_csv.name}.")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows({"old_key": k, "new_key": existing[k]} for k in sorted(existing))
        os.replace(temp_name, out_csv)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return {
        "aliases": len(existing),
        "new_aliases": len(proposed),
        "split_or_surviving": len(destinations) - len(proposed),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_csv", type=Path)
    parser.add_argument("new_csv", type=Path)
    parser.add_argument("out_csv", type=Path)
    args = parser.parse_args()
    print(build(args.old_csv, args.new_csv, args.out_csv))
