import asyncio

from hermes_cli import web_server


class _FakeSessionDB:
    """Fake backing the /api/sessions/search endpoint.

    The endpoint surfaces direct session-id matches first, then FTS message
    matches, deduping both by compression lineage root. This fake has no
    compression chains (get_session returns no parent), so each session is its
    own lineage root.
    """

    closed = False

    def search_sessions_by_id(self, query, limit=20, include_archived=True):
        assert query == "20260603"
        assert include_archived is True
        return [
            {
                "id": "20260603_090200_exact",
                "preview": "ID match preview",
                "source": "cli",
                "model": "claude",
                "started_at": 100,
            }
        ]

    def search_messages(self, query, limit=20):
        assert query == "20260603*"
        return [
            {
                "session_id": "20260603_090200_exact",
                "snippet": "duplicate content hit should not replace ID hit",
                "role": "user",
                "source": "cli",
                "model": "claude",
                "session_started": 100,
            },
            {
                "session_id": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
            },
        ]

    def get_session(self, session_id):
        # No compression chains in this fixture — every session is its own root.
        return {"id": session_id, "parent_session_id": None}

    def get_compression_tip(self, session_id):
        return session_id

    def get_message_bearing_tip(self, session_id):
        # Mirror SessionDB: no chain, sessions have messages → itself.
        return session_id

    def close(self):
        self.closed = True


def test_desktop_session_search_merges_id_matches_before_content_matches(monkeypatch):
    monkeypatch.setattr("hermes_state.SessionDB", _FakeSessionDB)

    response = asyncio.run(web_server.search_sessions(q="20260603", limit=2))

    # ID match surfaces first; the content hit on the SAME session is deduped
    # by lineage root (not double-listed); the unrelated content hit follows.
    assert response == {
        "results": [
            {
                "session_id": "20260603_090200_exact",
                "lineage_root": "20260603_090200_exact",
                "structural_tip": "20260603_090200_exact",
                "snippet": "ID match preview",
                "role": None,
                "source": "cli",
                "model": "claude",
                "session_started": 100,
            },
            {
                "session_id": "content_session",
                "lineage_root": "content_session",
                "structural_tip": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
            },
        ]
    }


class _EmptyTipChainDB:
    """Fake modelling the #15000 shape: a compression chain whose structural
    tip is an accounting-only row with zero messages, while an older segment
    (``mid``) still holds the real transcript that FTS matched.

    Chain: root -> mid (FTS hit lives here) -> tip (empty, structural tip).
    """

    closed = False

    # Full rows so compression_root() follows the compression edges
    # (parent end_reason='compression' and child started_at >= parent ended_at).
    _ROWS = {
        "root": {
            "id": "root", "parent_session_id": None,
            "started_at": 0, "ended_at": 100, "end_reason": "compression",
        },
        "mid": {
            "id": "mid", "parent_session_id": "root",
            "started_at": 101, "ended_at": 200, "end_reason": "compression",
        },
        "tip": {
            "id": "tip", "parent_session_id": "mid",
            "started_at": 201, "ended_at": None, "end_reason": None,
        },
    }

    def search_sessions_by_id(self, query, limit=20, include_archived=True):
        return []

    def search_messages(self, query, limit=20):
        # FTS matched a real message that lives in the OLDER ``mid`` segment.
        return [
            {
                "session_id": "mid",
                "snippet": "the matched content",
                "role": "assistant",
                "source": "cli",
                "model": "gpt",
                "session_started": 100,
            }
        ]

    def get_session(self, session_id):
        return self._ROWS.get(session_id)

    def get_compression_tip(self, session_id):
        # Structural tip of the chain is the empty accounting-only row.
        return "tip"

    def get_message_bearing_tip(self, session_id):
        # Newest segment that actually has messages is ``mid``, not ``tip``.
        return "mid"

    def close(self):
        self.closed = True


def test_search_result_opens_message_bearing_segment_not_empty_tip(monkeypatch):
    """A content hit in a chain with an empty structural tip must open the
    message-bearing segment (``mid``), exposing the empty ``tip`` only as the
    separate ``structural_tip`` pointer — the #15000 search-surface fix."""
    monkeypatch.setattr("hermes_state.SessionDB", _EmptyTipChainDB)

    response = asyncio.run(web_server.search_sessions(q="content", limit=5))

    assert response == {
        "results": [
            {
                "session_id": "mid",
                "lineage_root": "root",
                "structural_tip": "tip",
                "snippet": "the matched content",
                "role": "assistant",
                "source": "cli",
                "model": "gpt",
                "session_started": 100,
            }
        ]
    }
