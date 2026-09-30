"""Load and validate the state registry (states.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REGISTRY_PATH = Path(__file__).parent / "states.yaml"

SOURCES = {"upstream", "patched", "custom", "manual"}
STATUSES = {"unverified", "active", "broken", "manual_only", "archive"}
TIERS = {"easy", "medium", "hard", None}
CADENCES = {"daily", "weekly", None}
# How rows of one source document that share a legacy dedupe_key are read:
# `versions` folds them into one notice's versions (last wins); `distinct_rows`
# keeps each distinct row as its own entry (warnlive/normalize/entries.py).
SAME_KEY_POLICIES = {"versions", "distinct_rows"}
FRESHNESS_FIELDS = {
    "notice_date", "effective_date",
    "source_details.agency_reported_date",
    "source_details.agency_received_date",
    "source_details.agency_notification_date",
    "source_details.agency_posted_date",
}


@dataclass
class StateConfig:
    postal: str
    name: str
    source: str
    status: str
    tier: str | None
    cadence: str | None
    needs_browser: bool
    min_rows: int
    staleness_days: int | None
    expected_columns: list[str] | None
    source_url: str | None
    notes: str = ""
    freshness_field: str = "notice_date"
    # Wall-clock minutes a live fetch may run before the state is failed
    # and the run moves on; None = the pipeline default.
    fetch_budget_minutes: float | None = None
    same_key_policy: str = "versions"
    # raw_extra fields naming the source document a row was listed in, for
    # distinct_rows states whose capture concatenates several documents (CA's
    # fiscal-year reports). Distinct same-key rows are separate entries only
    # within one document; empty = the whole capture is one document.
    same_key_document_fields: list[str] | None = None
    same_key_document_order: str = "oldest_first"

    @property
    def scrapable(self) -> bool:
        return self.source != "manual"


@dataclass
class Registry:
    states: dict[str, StateConfig] = field(default_factory=dict)

    def __getitem__(self, postal: str) -> StateConfig:
        return self.states[postal.lower()]

    def __contains__(self, postal: str) -> bool:
        return postal.lower() in self.states

    def all(self) -> list[StateConfig]:
        return list(self.states.values())

    def for_run(
        self,
        states: list[str] | None = None,
        cadence: str | None = None,
        include_unverified: bool = False,
    ) -> list[StateConfig]:
        """Select states for a scrape run.

        Explicitly named states are returned regardless of status (so
        unverified/broken states can be exercised by hand). Otherwise only
        `active` states run — plus `unverified` when include_unverified is
        set. A cadence of "weekly" includes daily states (the weekly run is
        the full sweep); "daily" runs only daily states.
        """
        if states:
            missing = [s for s in states if s.lower() not in self.states]
            if missing:
                raise KeyError(f"Unknown state(s): {', '.join(missing)}")
            picked = [self[s] for s in states]
            not_scrapable = [c.postal for c in picked if not c.scrapable]
            if not_scrapable:
                raise ValueError(
                    f"Not scrapable (records-request only): {', '.join(not_scrapable)}"
                )
            return picked

        allowed = {"active"} | ({"unverified"} if include_unverified else set())
        result = [c for c in self.all() if c.scrapable and c.status in allowed]
        if cadence == "daily":
            result = [c for c in result if c.cadence == "daily"]
        return sorted(result, key=lambda c: (c.needs_browser, c.postal))


def load_registry(path: Path | None = None) -> Registry:
    path = path or REGISTRY_PATH
    raw = yaml.safe_load(path.read_text())
    states: dict[str, StateConfig] = {}
    errors: list[str] = []
    for postal, entry in raw.items():
        try:
            cfg = StateConfig(postal=postal.lower(), **entry)
        except TypeError as e:
            errors.append(f"{postal}: {e}")
            continue
        for check, msg in [
            (cfg.source in SOURCES, f"bad source {cfg.source!r}"),
            (cfg.status in STATUSES, f"bad status {cfg.status!r}"),
            (cfg.tier in TIERS, f"bad tier {cfg.tier!r}"),
            (cfg.cadence in CADENCES, f"bad cadence {cfg.cadence!r}"),
            (cfg.same_key_policy in SAME_KEY_POLICIES,
             f"bad same_key_policy {cfg.same_key_policy!r}"),
            (not cfg.same_key_document_fields or (
                cfg.same_key_policy == "distinct_rows"
                and isinstance(cfg.same_key_document_fields, list)
                and all(isinstance(f, str) and f for f in cfg.same_key_document_fields)),
             f"bad same_key_document_fields {cfg.same_key_document_fields!r}"),
            (cfg.same_key_document_order in {"oldest_first", "newest_first"},
             f"bad same_key_document_order {cfg.same_key_document_order!r}"),
            (cfg.freshness_field in FRESHNESS_FIELDS,
             f"bad freshness_field {cfg.freshness_field!r}"),
            (
                cfg.fetch_budget_minutes is None or (
                    isinstance(cfg.fetch_budget_minutes, (int, float))
                    and not isinstance(cfg.fetch_budget_minutes, bool)
                    and cfg.fetch_budget_minutes > 0
                ),
                f"bad fetch_budget_minutes {cfg.fetch_budget_minutes!r}",
            ),
            (
                (cfg.source == "manual") == (cfg.status == "manual_only"),
                "manual source and manual_only status must go together",
            ),
            (
                not cfg.scrapable or (cfg.min_rows > 0 and cfg.source_url),
                "scrapable states need min_rows > 0 and a source_url",
            ),
        ]:
            if not check:
                errors.append(f"{postal}: {msg}")
        states[cfg.postal] = cfg
    if errors:
        raise ValueError("Invalid states.yaml:\n" + "\n".join(errors))
    return Registry(states=states)
