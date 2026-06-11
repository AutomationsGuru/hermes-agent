#!/usr/bin/env python3
"""Materialize ~/.hermes/agent-logs.db for Mission Control's Agents view.

Mission Control (agent-os-project-packages/apps/mission-control/server.py)
reads ``HERMES_ROOT/agent-logs.db`` strictly read-only (``mode=ro&immutable=1``)
and expects one table::

    agent_logs(agent_name, task_description, model_used, status, created_at)

Nothing in Hermes produced that file, so the Overview source card showed
"degraded / agent-logs.db" and per-agent task/success counters fell back to
zeros (see the Mission Control HANDOFF "Known gaps"). This bridge derives the
table from Hermes session history — the same direction of data flow as the
session-stability work in plans/session-stability-improvement-2026-06-10.md:

* one row per LOGICAL conversation: compression chains (#15000 lineage) are
  collapsed onto their root, so token-burning continuation segments do not
  inflate per-agent task counts;
* ``agent_name`` is the Hermes lane: ``default`` for the root state.db,
  the profile directory name (apollo, father, eleusis, ...) for profile
  DBs — these substring-match Mission Control's AGENT_DEFINITIONS aliases;
* ``created_at`` is the chain root's ``started_at`` as timezone-aware UTC
  ISO-8601, the only format Mission Control's ``parse_timestamp`` accepts
  (epoch floats parse as None there);
* ``status`` maps the chain TIP's ``end_reason``; only deliberate closes
  ("cli_close"/"tui_close"/"new_session" -> "completed") land in Mission
  Control's SUCCESS_STATUSES — everything else stays a neutral status.

Privacy: only session TITLES are exported, never message content
(LOCAL_AGENTS.md constraint on session tooling output).

All source DBs are opened read-only (sqlite URI mode=ro). The output DB is
built in a temp file and atomically renamed over agent-logs.db so Mission
Control's per-request immutable readers never observe a half-written file.

Usage:
  python scripts/agent_logs_bridge.py             # build/refresh agent-logs.db
  python scripts/agent_logs_bridge.py --dry-run   # report what would be written
  python scripts/agent_logs_bridge.py --json      # machine-readable summary
  python scripts/agent_logs_bridge.py --hermes-home PATH --out PATH

Suited to a no-agent cron job, e.g.:
  hermes cron create agent-logs-bridge --schedule "*/15 * * * *" \
      --no-agent --script scripts/agent_logs_bridge.py
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Deliberate closes count as completed tasks on Mission Control's per-agent
# success counters; every other end_reason maps to a neutral status string.
_STATUS_BY_END_REASON = {
    None: "open",
    "cli_close": "completed",
    "tui_close": "completed",
    "new_session": "completed",
    "idle_timeout": "idle_timeout",
    "ws_orphan_reap": "orphan_reaped",
    "compression": "continued",  # tip-with-compression = continuation row lost
}

_SESSION_COLUMNS = (
    "id, parent_session_id, source, title, model, started_at, ended_at, "
    "end_reason, message_count, api_call_count"
)


def default_hermes_home() -> Path:
    """Mirror Mission Control's HERMES_ROOT resolution exactly:
    $HERMES_HOME if set, else the user home's .hermes."""
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".hermes"


def find_lane_dbs(home: Path) -> list[tuple[str, Path]]:
    """(lane_name, state.db path) for the default DB and every profile DB."""
    lanes = []
    root_db = home / "state.db"
    if root_db.exists():
        lanes.append(("default", root_db))
    profiles_dir = home / "profiles"
    if profiles_dir.is_dir():
        for p in sorted(profiles_dir.iterdir()):
            db = p / "state.db"
            if db.exists():
                lanes.append((p.name, db))
    return lanes


def load_sessions(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"SELECT {_SESSION_COLUMNS} FROM sessions").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _is_compression_edge(child: dict, parent: dict) -> bool:
    """Same edge test as SessionDB lineage walks: a child continues its
    parent only when the parent ended in compression before the child
    started. Branch/delegate children (other parent end_reasons) stay
    separate logical conversations."""
    return (
        parent.get("end_reason") == "compression"
        and parent.get("ended_at") is not None
        and child.get("started_at") is not None
        and child["started_at"] >= parent["ended_at"]
    )


def collapse_chains(sessions: list[dict]) -> list[dict]:
    """Group sessions into logical conversations keyed by compression root.

    Returns one record per chain with the root's start time and the newest
    segment's title/model/end_reason (falling back to older segments for
    title/model when the tip carries none — accounting-only tips from
    #15000 have no title of their own in some shapes).
    """
    by_id = {s["id"]: s for s in sessions}

    def root_of(sid: str) -> str:
        seen = set()
        cur = sid
        while cur not in seen:
            seen.add(cur)
            s = by_id.get(cur)
            parent_id = s.get("parent_session_id") if s else None
            parent = by_id.get(parent_id) if parent_id else None
            if not s or not parent or not _is_compression_edge(s, parent):
                return cur
            cur = parent_id
        return cur  # cycle guard — treat as its own root

    chains: dict[str, list[dict]] = {}
    for s in sessions:
        chains.setdefault(root_of(s["id"]), []).append(s)

    records = []
    for root_id, segments in chains.items():
        segments.sort(key=lambda s: s.get("started_at") or 0)
        root = by_id.get(root_id, segments[0])
        tip = segments[-1]
        title = next(
            (s["title"] for s in reversed(segments) if s.get("title")), None
        )
        model = next(
            (s["model"] for s in reversed(segments) if s.get("model")), None
        )
        records.append({
            "root_id": root_id,
            "source": root.get("source") or "cli",
            "title": title,
            "model": model,
            "started_at": root.get("started_at"),
            "tip_end_reason": tip.get("end_reason"),
            "segment_count": len(segments),
            "message_count": sum(s.get("message_count") or 0 for s in segments),
            "api_call_count": sum(s.get("api_call_count") or 0 for s in segments),
        })
    return records


def to_agent_log_row(lane: str, record: dict) -> tuple | None:
    """Map a logical conversation to an agent_logs row, or None to skip."""
    # Never-used rows (no messages, no API calls) are noise, not tasks.
    if not record["message_count"] and not record["api_call_count"]:
        return None
    started_at = record["started_at"]
    if started_at is None:
        return None
    created_at = datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat()
    end_reason = record["tip_end_reason"]
    status = _STATUS_BY_END_REASON.get(end_reason)
    if status is None:
        status = str(end_reason).lower()
    task = record["title"] or (
        f"(untitled {record['source']} session {record['root_id']})"
    )
    return (lane, task, record["model"] or "unknown", status, created_at)


def build_rows(home: Path) -> tuple[list[tuple], list[dict]]:
    rows: list[tuple] = []
    lane_summaries = []
    for lane, db_path in find_lane_dbs(home):
        sessions = load_sessions(db_path)
        records = collapse_chains(sessions)
        lane_rows = [
            r for r in (to_agent_log_row(lane, rec) for rec in records)
            if r is not None
        ]
        rows.extend(lane_rows)
        lane_summaries.append({
            "lane": lane,
            "db": str(db_path),
            "sessions": len(sessions),
            "logical_conversations": len(records),
            "rows": len(lane_rows),
        })
    rows.sort(key=lambda r: r[4])  # created_at ASC; readers ORDER BY DESC
    return rows, lane_summaries


def write_agent_logs_db(out_path: Path, rows: list[tuple]) -> None:
    """Write the full table to a temp file, then atomically replace."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".agent-logs-", suffix=".db.tmp", dir=str(out_path.parent)
    )
    os.close(fd)
    try:
        conn = sqlite3.connect(tmp_name)
        try:
            conn.execute(
                "CREATE TABLE agent_logs ("
                " agent_name TEXT NOT NULL,"
                " task_description TEXT,"
                " model_used TEXT,"
                " status TEXT,"
                " created_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX idx_agent_logs_created_at "
                "ON agent_logs(created_at)"
            )
            conn.executemany(
                "INSERT INTO agent_logs "
                "(agent_name, task_description, model_used, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_name, out_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hermes-home", type=Path,
                    help="Hermes home (default: $HERMES_HOME or ~/.hermes)")
    ap.add_argument("--out", type=Path,
                    help="output DB path (default: <hermes-home>/agent-logs.db)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written; write nothing")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    home = args.hermes_home or default_hermes_home()
    out_path = args.out or (home / "agent-logs.db")

    rows, lane_summaries = build_rows(home)
    if not args.dry_run:
        write_agent_logs_db(out_path, rows)

    summary = {
        "hermes_home": str(home),
        "out": str(out_path),
        "written": not args.dry_run,
        "total_rows": len(rows),
        "lanes": lane_summaries,
    }
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    mode = "DRY RUN" if args.dry_run else "WROTE"
    print(f"agent-logs bridge - {mode} {summary['total_rows']} rows -> {out_path}")
    for lane in lane_summaries:
        print(f"  {lane['lane']:<14} sessions={lane['sessions']:<4} "
              f"conversations={lane['logical_conversations']:<4} "
              f"rows={lane['rows']}")
    if not lane_summaries:
        print(f"  (no state.db found under {home})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
