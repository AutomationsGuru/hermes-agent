"""Regression tests for compression-related session stability.

Covers the two bugs evidenced on 2026-06-10 (see
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
"""

import time

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
