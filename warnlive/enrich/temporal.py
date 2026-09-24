"""Conservative, dated evidence for legal identities and ownership relationships.

This module is deliberately separate from ``Annotator``: a source observed on one
specified day is not evidence that a relationship held on adjacent days.  The
ledger contains review decisions, not automatic guesses from names or brands.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
RELATIONSHIP_TYPES = (
    "direct_parent",
    "ultimate_parent",
    "accounting_direct_parent",
    "accounting_ultimate_parent",
    "subsidiary_unspecified_depth",
)
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceError(ValueError):
    """The ledger or a pinned source fails validation."""


def _day(value: Any, field: str) -> date:
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        raise EvidenceError(f"{field} must be a full YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceError(f"{field} is not a valid date: {value}") from exc


def _unique_objects(items: Any, label: str) -> dict[str, dict]:
    if not isinstance(items, list):
        raise EvidenceError(f"{label} must be a list")
    result: dict[str, dict] = {}
    key = "entity_id" if label == "entities" else "source_id" if label == "sources" else "assertion_id"
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(key), str) or not item[key]:
            raise EvidenceError(f"{label} entries require {key}")
        if item[key] in result:
            raise EvidenceError(f"duplicate {key}: {item[key]}")
        result[item[key]] = item
    return result


@dataclass(frozen=True)
class IdentityResult:
    status: str
    entity_id: str | None = None
    assertion_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    review_statuses: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelationshipResult:
    status: str
    parent_entity_id: str | None = None
    assertion_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    review_statuses: tuple[str, ...] = ()


class Ledger:
    """Validated, pinned evidence; all date comparisons use exact days."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"invalid JSON: {self.path}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise EvidenceError(f"ledger requires schema_version {SCHEMA_VERSION}")
        self.sources = _unique_objects(data.get("sources"), "sources")
        self.entities = _unique_objects(data.get("entities"), "entities")
        self.identities = _unique_objects(data.get("identities"), "identities")
        self.relationships = _unique_objects(data.get("relationships"), "relationships")
        self._validate()

    def _validate(self) -> None:
        for source_id, source in self.sources.items():
            if not any(isinstance(source.get(field), str) and source[field]
                       for field in ("url", "origin")):
                raise EvidenceError(f"source {source_id} requires url or origin")
            rel_path = source.get("local_path")
            if not isinstance(rel_path, str) or not rel_path:
                raise EvidenceError(f"source {source_id} requires local_path")
            digest = source.get("sha256")
            if not isinstance(digest, str) or not _HEX_SHA256.fullmatch(digest):
                raise EvidenceError(f"source {source_id} requires lowercase SHA-256")
            artifact = (self.path.parent / rel_path).resolve()
            if not artifact.is_file():
                raise EvidenceError(f"source {source_id} artifact missing: {artifact}")
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
                raise EvidenceError(f"source {source_id} artifact SHA-256 mismatch")
            if source.get("published_date"):
                _day(source["published_date"], f"source {source_id} published_date")
            retrieved_at = source.get("retrieved_at")
            if not isinstance(retrieved_at, str):
                raise EvidenceError(f"source {source_id} requires retrieved_at")
            if _DAY.fullmatch(retrieved_at):
                _day(retrieved_at, f"source {source_id} retrieved_at")
            else:
                try:
                    stamp = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise EvidenceError(f"source {source_id} has invalid retrieved_at") from exc
                if stamp.tzinfo is None:
                    raise EvidenceError(f"source {source_id} retrieved_at requires timezone")
        for entity_id, entity in self.entities.items():
            if not isinstance(entity.get("name"), str) or not entity["name"]:
                raise EvidenceError(f"entity {entity_id} requires name")
            if not isinstance(entity.get("identifiers", {}), dict):
                raise EvidenceError(f"entity {entity_id} identifiers must be an object")
        for assertion_id, assertion in self.identities.items():
            if assertion.get("review_status", "pilot_provisional") not in ("pilot_provisional", "human_approved"):
                raise EvidenceError(f"identity {assertion_id} has unsupported review_status")
            for field in ("source_notice_id", "state", "filed_name", "entity_id", "source_id"):
                if not isinstance(assertion.get(field), str) or not assertion[field]:
                    raise EvidenceError(f"identity {assertion_id} requires {field}")
            if assertion["entity_id"] not in self.entities:
                raise EvidenceError(f"identity {assertion_id} references unknown entity")
            if assertion["source_id"] not in self.sources:
                raise EvidenceError(f"identity {assertion_id} references unknown source")
        for assertion_id, assertion in self.relationships.items():
            if assertion.get("review_status", "pilot_provisional") not in ("pilot_provisional", "human_approved"):
                raise EvidenceError(f"relationship {assertion_id} has unsupported review_status")
            for field in ("child_entity_id", "parent_entity_id", "source_id"):
                if not isinstance(assertion.get(field), str) or not assertion[field]:
                    raise EvidenceError(f"relationship {assertion_id} requires {field}")
            if assertion["child_entity_id"] not in self.entities or assertion["parent_entity_id"] not in self.entities:
                raise EvidenceError(f"relationship {assertion_id} references unknown entity")
            if assertion["child_entity_id"] == assertion["parent_entity_id"]:
                raise EvidenceError(f"relationship {assertion_id} is self-referential")
            if assertion["source_id"] not in self.sources:
                raise EvidenceError(f"relationship {assertion_id} references unknown source")
            if assertion.get("relationship_type") not in RELATIONSHIP_TYPES:
                raise EvidenceError(f"relationship {assertion_id} has unsupported type")
            if assertion.get("assertion_status", "observed") not in ("observed", "completed"):
                raise EvidenceError(f"relationship {assertion_id} is not an observed/completed fact")
            scope = assertion.get("temporal_scope")
            if not isinstance(scope, dict):
                raise EvidenceError(f"relationship {assertion_id} requires temporal_scope")
            if scope.get("kind") == "snapshot":
                _day(scope.get("as_of"), f"relationship {assertion_id} as_of")
            elif scope.get("kind") == "interval":
                start = _day(scope.get("from"), f"relationship {assertion_id} from")
                through = _day(scope.get("through"), f"relationship {assertion_id} through")
                if through < start:
                    raise EvidenceError(f"relationship {assertion_id} interval ends before it starts")
            else:
                raise EvidenceError(f"relationship {assertion_id} has unsupported temporal_scope")

    def identity(self, source_notice_id: str | None, state: str | None, filed_name: str | None) -> IdentityResult:
        """Resolve only an exact, notice-scoped legal entity assertion."""
        hits = sorted(
            (a for a in self.identities.values()
             if a["source_notice_id"] == source_notice_id
             and a["state"] == state and a["filed_name"] == filed_name),
            key=lambda a: a["assertion_id"],
        )
        if not hits:
            return IdentityResult("identity_unresolved")
        entities = {a["entity_id"] for a in hits}
        ids = tuple(a["assertion_id"] for a in hits)
        sources = tuple(sorted({a["source_id"] for a in hits}))
        reviews = tuple(sorted({a.get("review_status", "pilot_provisional") for a in hits}))
        if len(entities) != 1:
            return IdentityResult("conflict", assertion_ids=ids, source_ids=sources, review_statuses=reviews)
        return IdentityResult("accepted", next(iter(entities)), ids, sources, reviews)

    def relationship(self, child_entity_id: str, relationship_type: str, as_of: str | None) -> RelationshipResult:
        """Return a parent only when pinned evidence explicitly covers as_of."""
        if relationship_type not in RELATIONSHIP_TYPES:
            raise EvidenceError(f"unsupported relationship type: {relationship_type}")
        if not as_of:
            return RelationshipResult("date_missing")
        if not isinstance(as_of, str) or not _DAY.fullmatch(as_of):
            return RelationshipResult("date_not_precise")
        try:
            day = date.fromisoformat(as_of)
        except ValueError:
            return RelationshipResult("date_not_precise")
        hits = []
        for assertion in self.relationships.values():
            if assertion["child_entity_id"] != child_entity_id or assertion["relationship_type"] != relationship_type:
                continue
            scope = assertion["temporal_scope"]
            if scope["kind"] == "snapshot" and scope["as_of"] == as_of:
                hits.append(assertion)
            elif scope["kind"] == "interval" and _day(scope["from"], "from") <= day <= _day(scope["through"], "through"):
                hits.append(assertion)
        hits.sort(key=lambda a: a["assertion_id"])
        if not hits:
            return RelationshipResult("unknown")
        ids = tuple(a["assertion_id"] for a in hits)
        sources = tuple(sorted({a["source_id"] for a in hits}))
        reviews = tuple(sorted({a.get("review_status", "pilot_provisional") for a in hits}))
        parents = {a["parent_entity_id"] for a in hits}
        if len(parents) != 1:
            return RelationshipResult("conflict", assertion_ids=ids, source_ids=sources, review_statuses=reviews)
        return RelationshipResult("accepted", next(iter(parents)), ids, sources, reviews)
