"""Regression tests for compression-related session stability.

Covers the bugs evidenced on 2026-06-10 (see
``plans/session-stability-improvement-2026-06-10.md`` and issue #15000):

1. ``SessionDB.list_sessions_rich(min_message_count=N)`` applied the filter
   to the compression ROOT row in SQL, then projected the row forward to the
   structural chain tip. An accounting-only tip (zero persisted messages but
   live api_call/token counters) therefore leaked a ``message_count=0`` row
   past a ``min_message_count=1`` filter — observed live with chain root
   ``20260609_201805_5c1f9f`` -> tip ``20260610_001133_c3021c``.

2. ``AIAgent._flush_messages_to_session_db`` computed its start offset as
   ``max(len(conversation_history), _last_flushed_db_idx)``. Compression
   rotates ``agent.session_id`` to a fresh continuation row and resets the
   cursor to 0, but finalizer/error paths can still pass the STALE
   pre-compression ``conversation_history``. When that history is longer
   than the compressed message list, zero rows are appended to the new
   session while token counters keep accruing — producing the empty
   usage-bearing continuation rows in state.db.

3. The rotation block in ``compress_context`` originally performed the
   cursor reset / rotation flagging AFTER its fallible session-DB calls
   (``create_session`` / ``update_system_prompt``). A transient SQLite
   failure in between left the id rotated with stale bookkeeping — the
   same #15000 shape via a different door. The bookkeeping now happens
   immediately after the id swap; ``TestRotationBlockFailureWindow``
   pins it, including through the plugin-context-engine (hermes-lcm
   shaped) strict-signature ``compress`` fallback.
"""

import os
import time
from unittest.mock import patch

import pytest

from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = 1709500000.0


@pytest.fixture
def db(tmp_path):
    d = SessionDB(db_path=tmp_path / "state.db")
    yield d
    d.close()


def _set_times(db, sid, started_at, ended_at=None, end_reason=None):
    with db._lock:
        db._conn.execute(
            "UPDATE sessions SET started_at=? WHERE id=?", (started_at, sid)
        )
        if ended_at is not None:
            db._conn.execute(
                "UPDATE sessions SET ended_at=?, end_reason=? WHERE id=?",
                (ended_at, end_reason, sid),
            )
        db._conn.commit()


def _pin_message_times(db, sid, ts):
    with db._lock:
        db._conn.execute(
            "UPDATE messages SET timestamp=? WHERE session_id=?", (ts, sid)
        )
        db._conn.commit()


def _build_chain_with_empty_tip(db):
    """root (messages, compressed) -> mid (messages, compressed) -> tip
    (ZERO messages, but api_call/token usage counters — the #15000 shape).
    """
    db.create_session("root", "cli")
    db.append_message("root", "user", "kick off the cleanup work")
    db.append_message("root", "assistant", "on it")
    _pin_message_times(db, "root", T0 + 10)
    _set_times(db, "root", T0, ended_at=T0 + 100, end_reason="compression")

    db.create_session("mid", "cli", parent_session_id="root")
    db.append_message("mid", "user", "continue the cleanup")
    db.append_message("mid", "assistant", "continuing")
    _pin_message_times(db, "mid", T0 + 150)
    _set_times(db, "mid", T0 + 101, ended_at=T0 + 200, end_reason="compression")

    # Accounting-only tip: usage counters but no message rows.
    db.create_session("tip", "cli", parent_session_id="mid")
    _set_times(db, "tip", T0 + 201)
    db.update_token_counts("tip", input_tokens=250000, output_tokens=2300,
                           api_call_count=4)
    return "root", "mid", "tip"


# ---------------------------------------------------------------------------
# Bug 1 — projected empty tips must not leak past min_message_count
# ---------------------------------------------------------------------------


class TestMinMessageCountAfterProjection:
    def test_projected_row_honors_min_message_count(self, db):
        """min_message_count=1 must never return a final row with
        message_count=0 — the live-reproduced dashboard/recents bug."""
        _build_chain_with_empty_tip(db)
        rows = db.list_sessions_rich(min_message_count=1)
        assert rows, "chain should still surface (root/mid have messages)"
        for row in rows:
            assert (row.get("message_count") or 0) >= 1, (
                f"projected row {row.get('id')} leaked past "
                f"min_message_count=1 with message_count="
                f"{row.get('message_count')}"
            )

    def test_falls_back_to_latest_message_bearing_segment(self, db):
        """The surfaced row should be the NEWEST chain segment that actually
        has messages (mid), not the empty structural tip."""
        _build_chain_with_empty_tip(db)
        rows = db.list_sessions_rich(min_message_count=1)
        ids = [r["id"] for r in rows]
        assert "tip" not in ids
        assert "mid" in ids
        mid_row = next(r for r in rows if r["id"] == "mid")
        # Lineage metadata is preserved for callers that need the chain.
        assert mid_row.get("_lineage_root_id") == "root"
        assert mid_row.get("_structural_tip_id") == "tip"

    def test_order_by_last_active_path_also_honors_filter(self, db):
        """The dashboard recents endpoint uses order_by_last_active=True —
        the same guarantee must hold on the recursive-CTE path."""
        _build_chain_with_empty_tip(db)
        rows = db.list_sessions_rich(
            min_message_count=1, order_by_last_active=True
        )
        assert rows
        for row in rows:
            assert (row.get("message_count") or 0) >= 1

    def test_default_zero_min_keeps_structural_tip_projection(self, db):
        """No behavior change for callers that did not ask for non-empty
        sessions: the structural tip still wins by default."""
        _build_chain_with_empty_tip(db)
        rows = db.list_sessions_rich()
        ids = [r["id"] for r in rows]
        assert "tip" in ids
        tip_row = next(r for r in rows if r["id"] == "tip")
        assert tip_row.get("_lineage_root_id") == "root"

    def test_get_message_bearing_tip_walks_to_latest_nonempty(self, db):
        _build_chain_with_empty_tip(db)
        assert db.get_message_bearing_tip("root") == "mid"
        # Starting mid-chain works too.
        assert db.get_message_bearing_tip("mid") == "mid"

    def test_get_message_bearing_tip_none_when_chain_empty(self, db):
        db.create_session("lonely", "cli")
        assert db.get_message_bearing_tip("lonely") is None


# ---------------------------------------------------------------------------
# Bug 2 — stale conversation_history must not skip post-compression writes
# ---------------------------------------------------------------------------


class _FlushProbe:
    """Minimal AIAgent stand-in exposing exactly the attributes that
    ``AIAgent._flush_messages_to_session_db`` touches."""

    def __init__(self, db, session_id):
        self._session_db = db
        self._session_db_created = True
        self._last_flushed_db_idx = 0
        self._last_flushed_session_id = None
        self._session_rotated_since_flush = False
        self.session_id = session_id

    def _apply_persist_user_message_override(self, messages):
        return None

    def _ensure_db_session(self):
        return None


def _flush(probe, messages, conversation_history=None):
    from run_agent import AIAgent

    AIAgent._flush_messages_to_session_db(probe, messages, conversation_history)


class TestFlushCursorScopedBySession:
    def _make_db_with_root(self, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("rootsid", "cli")
        return db

    def test_stale_history_after_rotation_still_writes_child_messages(
        self, tmp_path
    ):
        """THE bug: compression rotated the session, cursor reset to 0, but a
        stale pre-compression history longer than the compressed message list
        suppressed every write to the new child session."""
        db = self._make_db_with_root(tmp_path)
        try:
            probe = _FlushProbe(db, "rootsid")
            # Turn 1: normal flush of 4 messages into the root session.
            history = [
                {"role": "user", "content": "start"},
                {"role": "assistant", "content": "ack"},
                {"role": "user", "content": "do the thing"},
                {"role": "assistant", "content": "done"},
            ]
            _flush(probe, history)
            assert len(db.get_messages("rootsid")) == 4
            assert probe._last_flushed_db_idx == 4

            # Compression rotates the session (mirrors
            # agent/conversation_compression.py): end old row, create child,
            # reset cursor, mark rotation.
            db.end_session("rootsid", "compression")
            db.create_session("contsid", "cli", parent_session_id="rootsid")
            probe.session_id = "contsid"
            probe._last_flushed_db_idx = 0
            probe._session_rotated_since_flush = True

            compressed = [
                {"role": "user", "content": "[compressed summary]"},
                {"role": "user", "content": "[todo snapshot]"},
            ]
            # Finalizer/error path passes the STALE 4-message history.
            _flush(probe, compressed, history)

            rows = db.get_messages("contsid")
            assert len(rows) == 2, (
                "post-compression flush wrote no rows — stale "
                "conversation_history suppressed the child session writes"
            )
            assert probe._last_flushed_session_id == "contsid"
            assert probe._session_rotated_since_flush is False
        finally:
            db.close()

    def test_session_id_mismatch_alone_triggers_rotation_handling(self, tmp_path):
        """Even without the explicit rotation flag, a flush cursor that was
        last advanced for a DIFFERENT session id must not let stale history
        suppress writes (covers code paths that rotate ids without setting
        the flag)."""
        db = self._make_db_with_root(tmp_path)
        try:
            probe = _FlushProbe(db, "rootsid")
            history = [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ]
            _flush(probe, history)
            assert probe._last_flushed_session_id == "rootsid"

            db.end_session("rootsid", "compression")
            db.create_session("contsid", "cli", parent_session_id="rootsid")
            probe.session_id = "contsid"
            probe._last_flushed_db_idx = 0
            # NOTE: rotation flag intentionally NOT set here.

            compressed = [{"role": "user", "content": "[summary]"}]
            _flush(probe, compressed, history)
            assert len(db.get_messages("contsid")) == 1
        finally:
            db.close()

    def test_resume_skip_of_already_persisted_history_still_works(self, tmp_path):
        """No regression: same-session flushes must keep skipping the
        conversation_history prefix (resume / fresh-gateway-agent case)."""
        db = self._make_db_with_root(tmp_path)
        try:
            probe = _FlushProbe(db, "rootsid")
            persisted = [
                {"role": "user", "content": "old 1"},
                {"role": "assistant", "content": "old 2"},
            ]
            # Simulate resume: history rows already exist in the DB.
            for m in persisted:
                db.append_message("rootsid", m["role"], m["content"])

            new_turn = persisted + [
                {"role": "user", "content": "new question"},
                {"role": "assistant", "content": "new answer"},
            ]
            _flush(probe, new_turn, persisted)

            rows = db.get_messages("rootsid")
            assert len(rows) == 4, "history prefix must be skipped, not re-written"
            contents = [r.get("content") for r in rows]
            assert contents.count("old 1") == 1
        finally:
            db.close()

    def test_repeated_flush_is_idempotent_after_rotation(self, tmp_path):
        """Multiple exit paths call _persist_session in one turn; after the
        rotation-aware first flush, repeats must not duplicate rows."""
        db = self._make_db_with_root(tmp_path)
        try:
            probe = _FlushProbe(db, "rootsid")
            db.end_session("rootsid", "compression")
            db.create_session("contsid", "cli", parent_session_id="rootsid")
            probe.session_id = "contsid"
            probe._session_rotated_since_flush = True

            stale_history = [{"role": "user", "content": f"old {i}"} for i in range(6)]
            compressed = [
                {"role": "user", "content": "[summary]"},
                {"role": "assistant", "content": "continuing"},
            ]
            _flush(probe, compressed, stale_history)
            _flush(probe, compressed, stale_history)

            assert len(db.get_messages("contsid")) == 2
        finally:
            db.close()

    def test_cursor_beyond_message_list_warns_and_clamps(self, tmp_path, caplog):
        """A flush cursor past the end of the live message list is only
        producible by a failed rotation or an engine shrinking the list in
        place — the precursor state to #15000. It must warn, not skip into
        negative-slice territory or crash."""
        import logging

        db = self._make_db_with_root(tmp_path)
        try:
            probe = _FlushProbe(db, "rootsid")
            probe._last_flushed_session_id = "rootsid"
            probe._last_flushed_db_idx = 10  # cursor from a longer, older list

            short = [{"role": "user", "content": "post-shrink"}]
            with caplog.at_level(logging.WARNING):
                _flush(probe, short)

            assert any("flush cursor" in r.message.lower() for r in caplog.records)
            # Cursor re-anchored to the live list; no rows written (the rows
            # the cursor pointed past were never these messages).
            assert probe._last_flushed_db_idx == 1
            assert db.get_messages("rootsid") == []
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Bug 3 — rotation bookkeeping must survive transient DB failures, for the
# built-in compressor AND plugin context engines (hermes-lcm shaped)
# ---------------------------------------------------------------------------


class _PluginShapedEngine:
    """Context engine with the EXACT published plugin contract surface
    (https://hermes-agent.nousresearch.com/docs/developer-guide/context-engine-plugin):
    strict ``compress(messages, current_tokens=None)`` signature — no
    ``focus_topic`` / ``force`` — so the host must take its TypeError
    fallback path, exactly as it does for hermes-lcm."""

    name = "lcm-shaped-test-engine"
    last_prompt_tokens = 0
    last_completion_tokens = 0
    last_total_tokens = 0
    threshold_tokens = 0
    context_length = 200_000
    compression_count = 0
    _last_compress_aborted = False
    _last_summary_error = None

    def __init__(self):
        self.session_start_calls = []
        self.strict_calls = 0

    def compress(self, messages, current_tokens=None):
        self.strict_calls += 1
        self.compression_count += 1
        return [
            {"role": "user", "content": "[CONTEXT COMPACTION] lcm summary"},
            {"role": "user", "content": "tail question"},
        ]

    def should_compress(self, prompt_tokens=None):
        return False

    def update_from_response(self, usage):
        pass

    def on_session_start(self, session_id, **kwargs):
        self.session_start_calls.append((session_id, kwargs))


class TestRotationBlockFailureWindow:
    def _make_agent(self, db, session_id="original-session"):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            from run_agent import AIAgent
            return AIAgent(
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                model="test/model",
                quiet_mode=True,
                session_db=db,
                session_id=session_id,
                skip_context_files=True,
                skip_memory=True,
            )

    def test_create_session_failure_no_longer_strands_cursor(self, tmp_path):
        """THE failure window: create_session raises (transient SQLite lock)
        inside the rotation block AFTER the id swap. The cursor reset and
        rotation flag must already be in place, and the next flush must
        retry row creation and write the compressed messages — instead of
        producing a usage-bearing row with zero messages (#15000)."""
        import sqlite3 as _sqlite3

        db = SessionDB(db_path=tmp_path / "state.db")
        try:
            agent = self._make_agent(db)
            engine = _PluginShapedEngine()
            agent.context_compressor = engine
            original_sid = agent.session_id

            # Seed the original session like a real first turn, advancing
            # the cursor past what the compressed list will hold.
            history = [{"role": "user", "content": f"m{i}"} for i in range(8)]
            agent._ensure_db_session()
            agent._flush_messages_to_session_db(history)
            assert agent._last_flushed_db_idx == 8

            def failing_create(*args, **kwargs):
                raise _sqlite3.OperationalError("database is locked")

            with patch.object(db, "create_session", side_effect=failing_create):
                compressed, _sp = agent._compress_context(
                    list(history), "sys", approx_tokens=10_000
                )

            # Id rotated; bookkeeping survived the create_session failure.
            assert agent.session_id != original_sid
            assert agent._session_rotated_since_flush is True
            assert agent._last_flushed_db_idx == 0
            assert agent._session_db_created is False
            # Retry path must carry the compression-lineage parent.
            assert agent._parent_session_id == original_sid

            # Finalizer passes the STALE pre-compression history; the flush
            # must retry row creation and still write the compressed rows.
            agent._flush_messages_to_session_db(compressed, history)
            rows = db.get_messages(agent.session_id)
            assert len(rows) == len(compressed), (
                "compressed messages were not persisted to the continuation "
                "session after a transient create_session failure"
            )
            child = db.get_session(agent.session_id)
            assert child["parent_session_id"] == original_sid
        finally:
            db.close()

    def test_plugin_engine_happy_path_rotation_bookkeeping(self, tmp_path):
        """hermes-lcm shaped engine, no failures: rotation goes through the
        strict-signature compress fallback, sets the rotation bookkeeping,
        fires the compression boundary hook, and a stale-history flush
        writes the compressed messages to the child row."""
        db = SessionDB(db_path=tmp_path / "state.db")
        try:
            agent = self._make_agent(db)
            engine = _PluginShapedEngine()
            agent.context_compressor = engine
            original_sid = agent.session_id

            history = [{"role": "user", "content": f"m{i}"} for i in range(8)]
            agent._ensure_db_session()
            agent._flush_messages_to_session_db(history)

            compressed, _sp = agent._compress_context(
                list(history), "sys", approx_tokens=10_000
            )

            assert engine.strict_calls == 1, (
                "host must fall back to the strict plugin compress signature"
            )
            assert agent.session_id != original_sid
            assert agent._session_rotated_since_flush is True
            assert agent._last_flushed_db_idx == 0
            # Boundary hook still fired for the plugin engine (hermes-lcm#68).
            comp_calls = [
                (sid, kw) for sid, kw in engine.session_start_calls
                if kw.get("boundary_reason") == "compression"
            ]
            assert comp_calls and comp_calls[-1][0] == agent.session_id

            agent._flush_messages_to_session_db(compressed, history)
            assert len(db.get_messages(agent.session_id)) == len(compressed)
        finally:
            db.close()
