"""Tests for scripts/agent_logs_bridge.py — the Mission Control agent-logs
materializer.

The contract under test is Mission Control's reader
(agent-os-project-packages/apps/mission-control/server.py):

* table ``agent_logs(agent_name, task_description, model_used, status,
  created_at)`` readable via ``mode=ro&immutable=1``;
* ``created_at`` must satisfy ``datetime.fromisoformat`` (epoch floats are
  silently dropped by its ``parse_timestamp``);
* only statuses in SUCCESS_STATUSES count toward per-agent success counters;
* compression chains (#15000 lineage) must collapse to ONE logical task row.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from hermes_state import SessionDB

# Mirror of Mission Control's success set (server.py line 228) — if Mission
# Control changes, this pin documents what the bridge was built against.
MISSION_CONTROL_SUCCESS_STATUSES = {
    "success", "succeeded", "done", "completed", "complete", "ok", "passed",
}

T0 = 1781000000.0


def _load_bridge():
    path = (
        Path(__file__).resolve().parents[2] / "scripts" / "agent_logs_bridge.py"
    )
    spec = importlib.util.spec_from_file_location("agent_logs_bridge", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge()


def _set_session(db, sid, *, started_at, ended_at=None, end_reason=None,
                 title=None, model=None, api_calls=0):
    with db._lock:
        db._conn.execute(
            "UPDATE sessions SET started_at=?, ended_at=?, end_reason=?, "
            "model=COALESCE(?, model), api_call_count=? WHERE id=?",
            (started_at, ended_at, end_reason, model, api_calls, sid),
        )
        db._conn.commit()
    if title:
        db.set_session_title(sid, title)


@pytest.fixture
def hermes_home(tmp_path):
    """Fake hermes home: default state.db with a compression chain + a live
    session, and one profile lane (apollo) with a single closed session."""
    home = tmp_path / "hermes"
    home.mkdir()

    db = SessionDB(db_path=home / "state.db")
    # Compression chain: root -> mid -> tip; tip closed via CLI.
    db.create_session("root", "cli", model="gpt-5.5")
    db.append_message("root", "user", "start the work")
    _set_session(db, "root", started_at=T0, ended_at=T0 + 100,
                 end_reason="compression", title="Workspace Cleanup",
                 api_calls=4)
    db.create_session("mid", "cli", parent_session_id="root", model="gpt-5.5")
    db.append_message("mid", "user", "continue")
    _set_session(db, "mid", started_at=T0 + 101, ended_at=T0 + 200,
                 end_reason="compression", title="Workspace Cleanup #2",
                 api_calls=5)
    db.create_session("tip", "cli", parent_session_id="mid", model="gpt-5.5")
    # Accounting-only tip (#15000 shape): no messages, usage only.
    _set_session(db, "tip", started_at=T0 + 201, ended_at=T0 + 300,
                 end_reason="cli_close", api_calls=3)

    # Independent live session (no end_reason).
    db.create_session("live", "tui", model="claude-fable-5")
    db.append_message("live", "user", "hello")
    _set_session(db, "live", started_at=T0 + 500, title="Live TUI Session",
                 api_calls=1)

    # Never-used noise row: no messages, no API calls — must be skipped.
    db.create_session("noise", "cli")
    _set_session(db, "noise", started_at=T0 + 600)
    db.close()

    apollo_dir = home / "profiles" / "apollo"
    apollo_dir.mkdir(parents=True)
    pdb = SessionDB(db_path=apollo_dir / "state.db")
    pdb.create_session("ap1", "cli", model="gpt-5.5")
    pdb.append_message("ap1", "user", "lane task")
    _set_session(pdb, "ap1", started_at=T0 + 50, ended_at=T0 + 80,
                 end_reason="cli_close", title="Apollo duty run", api_calls=2)
    pdb.close()
    return home


class TestBuildRows:
    def test_compression_chain_collapses_to_one_completed_row(self, hermes_home):
        rows, _ = bridge.build_rows(hermes_home)
        cleanup = [r for r in rows if "Workspace Cleanup" in (r[1] or "")]
        assert len(cleanup) == 1, (
            "compression chain must collapse to ONE logical task row"
        )
        agent_name, task, model, status, created_at = cleanup[0]
        assert agent_name == "default"
        # Newest title in the chain wins.
        assert task == "Workspace Cleanup #2"
        assert model == "gpt-5.5"
        # Tip closed via CLI -> completed -> counts as Mission Control success.
        assert status == "completed"
        assert status in MISSION_CONTROL_SUCCESS_STATUSES
        # created_at is the ROOT's start, as parseable tz-aware ISO-8601.
        parsed = datetime.fromisoformat(created_at)
        assert parsed.tzinfo is not None
        assert parsed.timestamp() == pytest.approx(T0)

    def test_live_session_is_neutral_open(self, hermes_home):
        rows, _ = bridge.build_rows(hermes_home)
        live = next(r for r in rows if r[1] == "Live TUI Session")
        assert live[3] == "open"
        assert live[3] not in MISSION_CONTROL_SUCCESS_STATUSES

    def test_profile_lane_maps_to_agent_name(self, hermes_home):
        rows, _ = bridge.build_rows(hermes_home)
        apollo = [r for r in rows if r[0] == "apollo"]
        assert len(apollo) == 1
        assert apollo[0][1] == "Apollo duty run"
        assert apollo[0][3] == "completed"

    def test_never_used_rows_are_skipped(self, hermes_home):
        rows, _ = bridge.build_rows(hermes_home)
        assert all("noise" not in (r[1] or "") for r in rows)
        # Exactly: collapsed chain + live + apollo.
        assert len(rows) == 3

    def test_untitled_sessions_get_placeholder_not_content(self, tmp_path):
        home = tmp_path / "h"
        home.mkdir()
        db = SessionDB(db_path=home / "state.db")
        db.create_session("s1", "cli")
        db.append_message("s1", "user", "SECRET message body")
        _set_session(db, "s1", started_at=T0, api_calls=1)
        db.close()
        rows, _ = bridge.build_rows(home)
        assert len(rows) == 1
        # Titles only — never message content.
        assert "SECRET" not in rows[0][1]
        assert "untitled" in rows[0][1]


class TestStatusMapping:
    @pytest.mark.parametrize("end_reason,expected", [
        (None, "open"),
        ("cli_close", "completed"),
        ("tui_close", "completed"),
        ("new_session", "completed"),
        ("idle_timeout", "idle_timeout"),
        ("ws_orphan_reap", "orphan_reaped"),
        ("compression", "continued"),
        ("Some_Future_Reason", "some_future_reason"),
    ])
    def test_end_reason_mapping(self, end_reason, expected):
        record = {
            "root_id": "x", "source": "cli", "title": "t", "model": "m",
            "started_at": T0, "tip_end_reason": end_reason,
            "segment_count": 1, "message_count": 1, "api_call_count": 1,
        }
        row = bridge.to_agent_log_row("default", record)
        assert row[3] == expected

    def test_only_deliberate_closes_count_as_success(self):
        success_statuses = {
            bridge.to_agent_log_row("d", {
                "root_id": "x", "source": "cli", "title": "t", "model": "m",
                "started_at": T0, "tip_end_reason": er,
                "segment_count": 1, "message_count": 1, "api_call_count": 1,
            })[3]
            for er in ("cli_close", "tui_close", "new_session")
        }
        neutral_statuses = {
            bridge.to_agent_log_row("d", {
                "root_id": "x", "source": "cli", "title": "t", "model": "m",
                "started_at": T0, "tip_end_reason": er,
                "segment_count": 1, "message_count": 1, "api_call_count": 1,
            })[3]
            for er in (None, "idle_timeout", "ws_orphan_reap", "compression")
        }
        assert success_statuses <= MISSION_CONTROL_SUCCESS_STATUSES
        assert not (neutral_statuses & MISSION_CONTROL_SUCCESS_STATUSES)


class TestWriteAndMissionControlRead:
    def test_output_readable_via_immutable_ro_like_mission_control(
        self, hermes_home, tmp_path
    ):
        out = tmp_path / "agent-logs.db"
        rows, _ = bridge.build_rows(hermes_home)
        bridge.write_agent_logs_db(out, rows)

        # Exactly Mission Control's open + query pattern (server.py 1008-1020).
        uri = f"file:{out.as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            got = conn.execute(
                "SELECT agent_name, task_description, model_used, status, "
                "created_at FROM agent_logs ORDER BY created_at DESC LIMIT 240"
            ).fetchall()
        finally:
            conn.close()
        assert len(got) == len(rows) == 3
        # Textual DESC ordering matches chronology for same-format ISO strings.
        created = [r[4] for r in got]
        assert created == sorted(created, reverse=True)
        # Every created_at passes Mission Control's parse_timestamp gate.
        for value in created:
            datetime.fromisoformat(value.replace("Z", "+00:00"))

    def test_rebuild_is_idempotent_atomic_replace(self, hermes_home, tmp_path):
        out = tmp_path / "agent-logs.db"
        rows, _ = bridge.build_rows(hermes_home)
        bridge.write_agent_logs_db(out, rows)
        first = out.read_bytes()
        bridge.write_agent_logs_db(out, rows)
        assert out.exists()
        conn = sqlite3.connect(f"file:{out.as_posix()}?mode=ro", uri=True)
        try:
            n = conn.execute("SELECT COUNT(*) FROM agent_logs").fetchone()[0]
        finally:
            conn.close()
        assert n == 3
        assert len(first) > 0
        # No stray temp files left behind.
        leftovers = list(tmp_path.glob(".agent-logs-*.db.tmp"))
        assert leftovers == []

    def test_main_dry_run_writes_nothing(self, hermes_home, tmp_path, capsys):
        out = tmp_path / "agent-logs.db"
        rc = bridge.main([
            "--hermes-home", str(hermes_home), "--out", str(out), "--dry-run",
        ])
        assert rc == 0
        assert not out.exists()
        assert "DRY RUN" in capsys.readouterr().out

    def test_main_json_summary(self, hermes_home, tmp_path, capsys):
        import json as _json
        out = tmp_path / "agent-logs.db"
        rc = bridge.main([
            "--hermes-home", str(hermes_home), "--out", str(out), "--json",
        ])
        assert rc == 0
        summary = _json.loads(capsys.readouterr().out)
        assert summary["total_rows"] == 3
        assert {lane["lane"] for lane in summary["lanes"]} == {
            "default", "apollo",
        }
        assert out.exists()
