"""Health report: data/health/status.json + health.md from state_runs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from warnlive.registry import Registry

VERDICT_ICON = {"ok": "✅", "degraded": "🟡", "failed": "❌", "skipped": "⏭️"}
CONSECUTIVE_FAILURES_FOR_BROKEN = 3
# A degraded state still ingests, so nothing else ever escalates it — a
# portal that quietly stops updating can sit yellow forever. After this many
# consecutive degraded runs the report treats it as worth a person's look.
CONSECUTIVE_DEGRADED_FOR_ATTENTION = 5


def build_status(conn: sqlite3.Connection, registry: Registry) -> dict:
    """Latest outcome + failure streak per state, from state_runs history."""
    states = {}
    for cfg in registry.all():
        postal = cfg.postal.upper()
        rows = conn.execute(
            "SELECT verdict, raw_rows, new_notices, updated_notices, checks_json, "
            "       error, finished_at "
            "FROM state_runs WHERE state = ? ORDER BY id DESC LIMIT 10",
            (postal,),
        ).fetchall()
        latest = rows[0] if rows else None
        streak = 0
        for r in rows:
            if r["verdict"] == "failed":
                streak += 1
            else:
                break
        degraded_streak = 0
        for r in rows:
            if r["verdict"] == "degraded":
                degraded_streak += 1
            else:
                break
        last_success = conn.execute(
            "SELECT finished_at FROM state_runs "
            "WHERE state = ? AND verdict IN ('ok','degraded') ORDER BY id DESC LIMIT 1",
            (postal,),
        ).fetchone()
        notices = conn.execute(
            "SELECT COUNT(*) AS c FROM notices WHERE state = ?", (postal,)
        ).fetchone()["c"]
        states[postal] = {
            "name": cfg.name,
            "registry_status": cfg.status,
            "source": cfg.source,
            "notices": notices,
            "latest_verdict": latest["verdict"] if latest else None,
            "latest_run": latest["finished_at"] if latest else None,
            "latest_error": latest["error"] if latest else None,
            "latest_checks": json.loads(latest["checks_json"])
            if latest and latest["checks_json"]
            else None,
            "last_success": last_success["finished_at"] if last_success else None,
            "consecutive_failures": streak,
            "consecutive_degraded": degraded_streak,
            "recommend_broken": streak >= CONSECUTIVE_FAILURES_FOR_BROKEN
            and cfg.status == "active",
            "chronically_degraded": (
                degraded_streak >= CONSECUTIVE_DEGRADED_FOR_ATTENTION
                and cfg.status == "active"
            ),
        }
    return states


def write_health(conn: sqlite3.Connection, registry: Registry, health_dir: Path) -> dict:
    health_dir = Path(health_dir)
    health_dir.mkdir(parents=True, exist_ok=True)
    status = build_status(conn, registry)

    (health_dir / "status.json").write_text(json.dumps(status, indent=2) + "\n")

    lines = [
        "# WARN pipeline health",
        "",
        "| State | Registry | Latest run | Verdict | Notices | Fail streak | Notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for postal in sorted(status):
        s = status[postal]
        icon = VERDICT_ICON.get(s["latest_verdict"] or "", "—")
        note = ""
        if s["recommend_broken"]:
            note = f"**recommend marking broken** ({s['consecutive_failures']} consecutive failures)"
        elif s["chronically_degraded"]:
            note = (f"**chronically degraded** ({s['consecutive_degraded']} "
                    "consecutive degraded runs)")
        elif s["latest_error"]:
            note = s["latest_error"][:120]
        elif s["latest_checks"]:
            warns = [
                c["name"]
                for c in s["latest_checks"]["checks"]
                if c["outcome"] != "pass"
            ]
            note = ", ".join(warns)
        lines.append(
            f"| {postal} | {s['registry_status']} | {s['latest_run'] or '—'} "
            f"| {icon} {s['latest_verdict'] or '—'} | {s['notices']} "
            f"| {s['consecutive_failures']} | {note} |"
        )
    (health_dir / "health.md").write_text("\n".join(lines) + "\n")
    return status
