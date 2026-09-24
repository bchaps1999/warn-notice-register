"""Evidence-based classification of repeated agency source observations.

The returned disposition describes a source row, not a second public notice.
An agency ID establishes a filing group, but a repeated ID alone does not
establish that differing sites or layoff phases are revisions.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Disposition:
    kind: str  # notice, revision, duplicate_capture, or unresolved
    evidence: str
    related_row: str | None = None


def classify_agency_ids(
    rows: Iterable[T], *, row_key: Callable[[T], str],
    agency_id: Callable[[T], str | None], site: Callable[[T], str | None],
    action: Callable[[T], str | None], revision: Callable[[T], bool],
    content: Callable[[T], str], preferred: Callable[[T], bool] | None = None,
) -> dict[str, Disposition]:
    """Select one observation per agency ID when its meaning is unambiguous.

    An original is preferred when present, preserving the filed observation.
    Explicit later revisions and identical captures are linked to that row.
    Unmarked content conflicts, multiple sites, and multiple phases are held.
    """
    groups: dict[str, list[T]] = defaultdict(list)
    result: dict[str, Disposition] = {}
    for row in rows:
        ident = agency_id(row)
        if ident:
            groups[ident].append(row)
        else:
            result[row_key(row)] = Disposition("unresolved", "missing_agency_id")
    for group in groups.values():
        group.sort(key=row_key)
        if len(group) == 1:
            result[row_key(group[0])] = Disposition("notice", "unique_agency_id")
            continue
        sites = {site(row) for row in group if site(row)}
        actions = {action(row) for row in group if action(row)}
        originals = [row for row in group if not revision(row)]
        if len(sites) > 1 or (len(actions) > 1 and not any(revision(row) for row in group)):
            reason = "multiple_sites_under_agency_id" if len(sites) > 1 else "multiple_phases_under_agency_id"
            result.update((row_key(row), Disposition("unresolved", reason)) for row in group)
            continue
        distinct_originals = {content(row) for row in originals}
        if len(distinct_originals) > 1:
            result.update((row_key(row), Disposition("unresolved", "conflicting_originals_under_agency_id"))
                           for row in group)
            continue
        preferred_rows = [row for row in group if preferred is not None and preferred(row)]
        if not originals and len({content(row) for row in group}) > 1 and len(preferred_rows) != 1:
            # An amendment label establishes lineage, not which revision is
            # current. Without an original or ordered agency version marker,
            # choosing one would depend on a source-row pointer rather than
            # filing evidence.
            result.update((row_key(row), Disposition("unresolved", "unordered_revisions_under_agency_id"))
                          for row in group)
            continue
        selected = (originals or preferred_rows or group)[0]
        selected_key = row_key(selected)
        basis = ("agency_id_preferred_original_label" if not originals and preferred_rows else
                 "agency_id_with_revisions_or_captures")
        result[selected_key] = Disposition("notice", basis)
        for row in group:
            key = row_key(row)
            if key == selected_key:
                continue
            kind = "duplicate_capture" if content(row) == content(selected) else (
                "revision" if revision(row) else "unresolved")
            evidence = ("identical_agency_id_content" if kind == "duplicate_capture" else
                        "explicit_revision_marker_and_agency_id" if kind == "revision" else
                        "unmarked_change_under_agency_id")
            result[key] = Disposition(kind, evidence, selected_key)
    return result


def classify_idless_events(
    rows: Iterable[T], *, row_key: Callable[[T], str],
    employer: Callable[[T], str], site: Callable[[T], str],
    action: Callable[[T], str | None], notice: Callable[[T], str | None],
    content: Callable[[T], str],
) -> dict[str, Disposition]:
    """Separate ID-less events using employer, site, and reported event dates.

    A matching event signature with changed content is a *possible* revision,
    not a confirmed one; all such rows remain unresolved. Exact repeated
    content is one observation plus duplicate captures.
    """
    indexed: list[T] = []
    result: dict[str, Disposition] = {}
    for row in rows:
        key = row_key(row)
        firm, place = employer(row), site(row)
        if not firm or not place or not notice(row):
            result[key] = Disposition("unresolved", "insufficient_event_identity")
        else:
            indexed.append(row)
    parents = list(range(len(indexed)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    seen: dict[tuple[str, str, str, str], int] = {}
    for index, row in enumerate(indexed):
        firm, place = employer(row), site(row)
        # Either a shared action date or a shared notice date can indicate a
        # revised dashboard observation. Different dates on both axes are
        # distinct events, even when the employer recurs.
        axes = [("notice", notice(row))]
        if action(row):
            axes.append(("action", action(row)))
        for axis, value in axes:
            signature = (firm, place, axis, value)
            if signature in seen:
                parents[root(index)] = root(seen[signature])
            else:
                seen[signature] = index
    groups: dict[int, list[T]] = defaultdict(list)
    for index, row in enumerate(indexed):
        groups[root(index)].append(row)
    for group in groups.values():
        group.sort(key=row_key)
        first = group[0]
        first_key = row_key(first)
        if len({content(row) for row in group}) > 1:
            result.update((row_key(row), Disposition("unresolved", "possible_revision_same_event"))
                          for row in group)
            continue
        result[first_key] = Disposition("notice", "distinct_employer_site_event")
        for row in group[1:]:
            result[row_key(row)] = Disposition("duplicate_capture", "identical_event_content", first_key)
    return result
