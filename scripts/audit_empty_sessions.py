#!/usr/bin/env python3
"""Audit (and optionally quarantine) empty usage-bearing session rows.

Compression bug #15000 (fixed 2026-06-10, see
``plans/session-stability-improvement-2026-06-10.md``) produced continuation
session rows that carry api_call/token counters but ZERO persisted message
rows. The runtime fix stops new ones; this script handles the rows that
already exist.

Default is a READ-ONLY dry run. ``--apply`` marks affected rows:
  * sets ``archived = 1`` (hides them from user-facing lists; they are
    chain children so most lists already hide them — this is belt and
    suspenders for admin/raw views)
  * stamps ``model_config`` with ``"_empty_continuation": true`` so the
    rows are identifiable as accounting-only lineage records forever.

No rows are ever deleted: they hold token/cost accounting and keep the
``parent_session_id`` lineage chain intact. Live structural tips (rows
with ``ended_at IS NULL``) are never touched even under --apply — an
in-flight session may still be writing to them.

Usage:
  python scripts/audit_empty_sessions.py                 # dry run, default profile
  python scripts/audit_empty_sessions.py --all-profiles  # dry run, every profile
  python scripts/audit_empty_sessions.py --apply         # quarantine (after review)
  python scripts/audit_empty_sessions.py --db PATH       # explicit state.db
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path


def find_profile_dbs(all_profiles: bool) -> list[tuple[str, Path]]:
    home = Path.home() / ".hermes"
    targets = [("default", home / "state.db")]
    if all_profiles:
        profiles_dir = home / "profiles"
        if profiles_dir.is_dir():
            for p in sorted(profiles_dir.iterdir()):
                db = p / "state.db"
                if db.exists():
                    targets.append((p.name, db))
    return [(name, db) for name, db in targets if db.exists()]


def audit_db(db_path: Path) -> list[dict]:
    """Return affected rows: zero messages but nonzero usage counters."""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT s.id, s.parent_session_id, s.source, s.title, s.model,
                   s.started_at, s.ended_at, s.end_reason, s.archived,
                   s.message_count, s.api_call_count,
                   s.input_tokens, s.output_tokens,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id)
                       AS actual_messages,
                   json_extract(s.model_config, '$._empty_continuation')
                       AS already_marked
            FROM sessions s
            WHERE s.message_count = 0
              AND (s.api_call_count > 0
                   OR s.input_tokens > 0
                   OR s.output_tokens > 0)
              AND NOT EXISTS (
                  SELECT 1 FROM messages m WHERE m.session_id = s.id
              )
            ORDER BY s.started_at
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def apply_quarantine(db_path: Path, ids: list[str]) -> int:
    """Archive + stamp the given rows. Returns number updated."""
    conn = sqlite3.connect(db_path.as_posix())
    try:
        updated = 0
        for sid in ids:
            cur = conn.execute(
                """
                UPDATE sessions
                SET archived = 1,
                    model_config = json_set(
                        COALESCE(model_config, '{}'),
                        '$._empty_continuation', json('true'),
                        '$._empty_continuation_marked_at', ?
                    )
                WHERE id = ? AND ended_at IS NOT NULL
                """,
                (time.time(), sid),
            )
            updated += cur.rowcount
        conn.commit()
        return updated
    finally:
        conn.close()


def fmt_ts(ts) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, help="explicit state.db path")
    ap.add_argument("--all-profiles", action="store_true",
                    help="scan every profile under ~/.hermes/profiles too")
    ap.add_argument("--apply", action="store_true",
                    help="archive + stamp affected rows (default: dry run)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.db:
        targets = [("explicit", args.db)]
    else:
        targets = find_profile_dbs(args.all_profiles)

    report = []
    for name, db_path in targets:
        rows = audit_db(db_path)
        live = [r for r in rows if r["ended_at"] is None]
        actionable = [r for r in rows if r["ended_at"] is not None
                      and not r["already_marked"]]
        entry = {
            "profile": name,
            "db": str(db_path),
            "affected_total": len(rows),
            "live_tips_skipped": len(live),
            "already_marked": sum(1 for r in rows if r["already_marked"]),
            "actionable": len(actionable),
            "rows": rows,
        }
        if args.apply and actionable:
            entry["updated"] = apply_quarantine(
                db_path, [r["id"] for r in actionable]
            )
        report.append(entry)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"Empty usage-bearing session audit — {mode}")
    print("=" * 70)
    for entry in report:
        print(f"\nProfile: {entry['profile']}  ({entry['db']})")
        print(f"  affected: {entry['affected_total']}   "
              f"actionable: {entry['actionable']}   "
              f"already marked: {entry['already_marked']}   "
              f"live tips skipped: {entry['live_tips_skipped']}")
        for r in entry["rows"]:
            flags = []
            if r["ended_at"] is None:
                flags.append("LIVE-SKIP")
            if r["already_marked"]:
                flags.append("MARKED")
            if r["archived"]:
                flags.append("ARCHIVED")
            flag_s = f" [{','.join(flags)}]" if flags else ""
            print(f"    {r['id']}  src={r['source']}  "
                  f"api={r['api_call_count']}  in={r['input_tokens']}  "
                  f"out={r['output_tokens']}  start={fmt_ts(r['started_at'])}  "
                  f"title={r['title'] or '-'}{flag_s}")
        if args.apply:
            print(f"  -> updated: {entry.get('updated', 0)}")
    if not args.apply:
        print("\nDry run only. Re-run with --apply to archive + stamp the "
              "actionable rows (never deletes, never touches live tips).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
