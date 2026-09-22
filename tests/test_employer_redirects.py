import csv

import pytest

from warnlive.migrate.employer_redirects import build


def _write(path, rows):
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["dedupe_key", "employer_key"])
        writer.writeheader()
        writer.writerows(rows)


def test_only_gone_one_to_one_employer_keys_become_aliases(tmp_path):
    old, new, out = (tmp_path / n for n in ("old.csv", "new.csv", "aliases.csv"))
    _write(old, [
        {"dedupe_key": "a", "employer_key": "old-gone"},
        {"dedupe_key": "b", "employer_key": "old-split"},
        {"dedupe_key": "c", "employer_key": "old-split"},
        {"dedupe_key": "d", "employer_key": "old-survives"},
        {"dedupe_key": "e", "employer_key": "old-survives"},
    ])
    _write(new, [
        {"dedupe_key": "a", "employer_key": "new-one"},
        {"dedupe_key": "b", "employer_key": "new-two"},
        {"dedupe_key": "c", "employer_key": "new-three"},
        {"dedupe_key": "d", "employer_key": "old-survives"},
        {"dedupe_key": "e", "employer_key": "new-four"},
    ])
    assert build(old, new, out) == {
        "aliases": 1, "new_aliases": 1, "split_or_surviving": 2,
    }
    with out.open(newline="") as fh:
        assert list(csv.DictReader(fh)) == [
            {"old_key": "old-gone", "new_key": "new-one"}
        ]


def test_notice_key_changes_refuse_redirect_generation(tmp_path):
    old, new, out = (tmp_path / n for n in ("old.csv", "new.csv", "aliases.csv"))
    _write(old, [{"dedupe_key": "a", "employer_key": "old"}])
    _write(new, [{"dedupe_key": "b", "employer_key": "new"}])
    with pytest.raises(ValueError, match="notice key sets differ"):
        build(old, new, out)
    assert not out.exists()
