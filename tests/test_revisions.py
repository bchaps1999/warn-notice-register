from warnlive.normalize.revisions import classify_agency_ids, classify_idless_events


def test_agency_id_marks_explicit_revision_but_holds_sites_and_unmarked_changes():
    rows = [
        {"key": "original", "id": "A", "site": "one", "action": "2020-01-01", "revision": False, "body": "127"},
        {"key": "update", "id": "A", "site": "one", "action": "2020-01-01", "revision": True, "body": "2"},
        {"key": "site1", "id": "B", "site": "one", "action": "2020-01-01", "revision": False, "body": "10"},
        {"key": "site2", "id": "B", "site": "two", "action": "2020-01-01", "revision": False, "body": "20"},
        {"key": "phase1", "id": "C", "site": "one", "action": "2020-01-01", "revision": False, "body": "10"},
        {"key": "phase2", "id": "C", "site": "one", "action": "2020-02-01", "revision": False, "body": "20"},
    ]
    decisions = classify_agency_ids(
        rows, row_key=lambda r: r["key"], agency_id=lambda r: r["id"],
        site=lambda r: r["site"], action=lambda r: r["action"],
        revision=lambda r: r["revision"], content=lambda r: r["body"],
    )
    assert decisions["original"].kind == "notice"
    assert decisions["update"].kind == "revision"
    assert decisions["update"].related_row == "original"
    assert all(decisions[key].kind == "unresolved" for key in ("site1", "site2", "phase1", "phase2"))


def test_idless_event_uses_site_and_both_event_dates():
    rows = [
        {"key": "first", "site": "one", "action": "2020-01-01", "notice": "2019-11-01", "body": "10"},
        {"key": "possible_update", "site": "one", "action": "2020-01-01", "notice": "2019-12-01", "body": "20"},
        {"key": "later", "site": "one", "action": "2021-01-01", "notice": "2020-11-01", "body": "30"},
        {"key": "other_site", "site": "two", "action": "2020-01-01", "notice": "2019-11-01", "body": "40"},
    ]
    decisions = classify_idless_events(
        rows, row_key=lambda r: r["key"], employer=lambda r: "same employer",
        site=lambda r: r["site"], action=lambda r: r["action"],
        notice=lambda r: r["notice"], content=lambda r: r["body"],
    )
    assert decisions["first"].evidence == "possible_revision_same_event"
    assert decisions["possible_update"].kind == "unresolved"
    assert decisions["later"].kind == "notice"
    assert decisions["other_site"].kind == "notice"
    reversed_decisions = classify_idless_events(
        reversed(rows), row_key=lambda r: r["key"], employer=lambda r: "same employer",
        site=lambda r: r["site"], action=lambda r: r["action"],
        notice=lambda r: r["notice"], content=lambda r: r["body"],
    )
    assert reversed_decisions == decisions


def test_identical_agency_captures_choose_stable_survivor():
    rows = [{"key": "z", "id": "A"}, {"key": "a", "id": "A"}]
    classify = lambda items: classify_agency_ids(
        items, row_key=lambda r: r["key"], agency_id=lambda r: r["id"],
        site=lambda r: "same", action=lambda r: "2020-01-01",
        revision=lambda r: False, content=lambda r: "identical",
    )
    assert classify(rows) == classify(reversed(rows))
    assert classify(rows)["a"].kind == "notice"


def test_unordered_revision_only_group_is_not_selected_by_row_pointer():
    rows = [{"key": "revision_a", "body": "127"},
            {"key": "revision_b", "body": "2"}]
    decisions = classify_agency_ids(
        rows, row_key=lambda r: r["key"], agency_id=lambda r: "A",
        site=lambda r: "same", action=lambda r: "2020-01-01",
        revision=lambda r: True, content=lambda r: r["body"],
    )
    assert {decision.kind for decision in decisions.values()} == {"unresolved"}
    assert {decision.evidence for decision in decisions.values()} == {
        "unordered_revisions_under_agency_id"}
    labeled = classify_agency_ids(
        rows, row_key=lambda r: r["key"], agency_id=lambda r: "A",
        site=lambda r: "same", action=lambda r: "2020-01-01",
        revision=lambda r: True, content=lambda r: r["body"],
        preferred=lambda r: r["key"] == "revision_a",
    )
    assert labeled["revision_a"].kind == "notice"
    assert labeled["revision_a"].evidence == "agency_id_preferred_original_label"
    assert labeled["revision_b"].related_row == "revision_a"
