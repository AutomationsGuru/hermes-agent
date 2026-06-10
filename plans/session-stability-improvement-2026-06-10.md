# Hermes session stability improvement plan

Generated: 2026-06-10 00:12 MDT
Repo: C:/Users/RDP/.hermes/hermes-agent
DB inspected: C:/Users/RDP/.hermes/state.db

## Goal

Make Hermes local session history stable and trustworthy across CLI, TUI/dashboard, session_search, compression continuations, and resume flows.

Specific target outcomes:

1. No session list or dashboard recents row should show an empty compression tip when the user asks for non-empty sessions.
2. Compression continuations should persist the compressed handoff/transcript rows into the active child session instead of recording only API/token counters.
3. Resume/read/search should land on the nearest session segment that actually has messages, not a zero-message head.
4. Existing empty compression chains should be diagnosable and, where safe, repaired or hidden without losing lineage metadata.
5. Regression tests should cover the observed failure mode before implementation is considered done.

## Evidence gathered

### Current DB facts

Read-only SQLite inspection found a live bad chain rooted at:

- root: `20260609_201805_5c1f9f`
- visible projected tip: `20260610_001133_c3021c`
- title lineage: `Workspace Cleanup and Archiving #19` through `#40`
- source: `cli`
- model: `gpt-5.5`

The chain has 19 segments. The first 8 segments have messages. The last 11 segments have:

- `message_count = 0`
- actual `messages` rows = 0
- nonzero `api_call_count`
- nonzero `input_tokens` / `output_tokens`

Representative empty segments:

- `20260609_233650_0da05f`, title `Workspace Cleanup and Archiving #30`, `message_count=0`, `api_call_count=9`, `input_tokens=435281`, `output_tokens=2669`, `end_reason=compression`
- `20260610_000050_acc99c`, title `Workspace Cleanup and Archiving #37`, `message_count=0`, `api_call_count=6`, `input_tokens=284691`, `output_tokens=2960`, `end_reason=compression`
- `20260610_001133_c3021c`, title `Workspace Cleanup and Archiving #40`, `message_count=0`, `api_call_count=4`, `input_tokens=251204`, `output_tokens=2356`, `end_reason=NULL` (current/live tip)

This proves work happened in those session IDs, but message rows were not flushed to those session IDs.

A second affected chain was also found:

- root: `20260609_231746_0ef5e3`
- segments: 5
- zero-message segments with usage: 1
- current tip: `20260610_000013_c2c932`, `message_count=158`

That suggests the bug is path-dependent/intermittent: an empty segment can occur and later segments may still recover message persistence.

### Live list_sessions_rich behavior

A direct call to:

```python
db.list_sessions_rich(
    source="cli",
    exclude_sources=["tool"],
    limit=10,
    min_message_count=1,
    order_by_last_active=True,
)
```

returned this first row:

```json
{
  "id": "20260610_001133_c3021c",
  "root": "20260609_201805_5c1f9f",
  "title": "Workspace Cleanup and Archiving #40",
  "message_count": 0,
  "parent": null,
  "end_reason": null,
  "preview": ""
}
```

That proves `min_message_count=1` does not guarantee the final returned/projected row has messages.

### Relevant implementation facts

#### Compression rotation

`agent/conversation_compression.py:501-551`:

- gets old title
- calls `agent.commit_memory_session(messages)`
- ends old session with `end_reason="compression"`
- rotates `agent.session_id` to a new ID
- updates gateway/logging session context
- creates a new child session with `parent_session_id=old_session_id`
- propagates title with auto-numbering
- updates system prompt for the new session
- resets `agent._last_flushed_db_idx = 0`

This confirms compression is meant to split DB rows and start a fresh child.

#### Message persistence

`run_agent.py:1466-1475`:

- `_persist_session()` stores `_session_messages`, saves the JSON log, then calls `_flush_messages_to_session_db(messages, conversation_history)`.

`run_agent.py:1535-1592`:

```python
start_idx = len(conversation_history) if conversation_history else 0
flush_from = max(start_idx, self._last_flushed_db_idx)
for msg in messages[flush_from:]:
    self._session_db.append_message(session_id=self.session_id, ...)
self._last_flushed_db_idx = len(messages)
```

This is the strongest root-cause evidence. After compression, `_last_flushed_db_idx` is reset to 0, but if the caller still passes the pre-compression `conversation_history`, then `start_idx = len(conversation_history)` can be larger than the compressed `messages` list. The loop writes zero rows. Token counters still update separately on the new `agent.session_id`, creating exactly the observed state: zero messages, nonzero usage.

#### Conversation-loop mitigation is incomplete

`agent/conversation_loop.py` has comments around compression paths saying compression created a new session and `conversation_history = None` should be set so `_flush_messages_to_session_db` writes compressed messages to the new session.

That mitigation exists in some compression branches, but the observed data proves at least one runtime path still reaches `_persist_session(messages, conversation_history)` with stale/non-None `conversation_history` after a session rotation.

#### Turn finalizer

`agent/turn_finalizer.py:138-144` always calls:

```python
agent._persist_session(messages, conversation_history)
```

If `conversation_history` is stale after compression, this finalizer preserves the skip behavior.

#### Dashboard/session list data flow

`hermes_cli/web_server.py:1801-1839`, `/api/sessions`:

- reads `min_messages`
- passes it to `db.list_sessions_rich(..., min_message_count=min_message_count, order_by_last_active=order == "recent")`
- returns rows directly after adding `is_active` and bool `archived`

`hermes_cli/web_server.py:1847-1963`, `/api/profiles/sessions`:

- same pattern across profiles
- opens profile DBs read-only
- passes `min_message_count` into `list_sessions_rich`
- merges/sorts rows by `last_active` or `started_at`

Because `list_sessions_rich` filters before projection, dashboard recents can display a projected zero-message tip despite `min_messages=1`.

#### Dashboard search data flow

`hermes_cli/web_server.py:1966-2126`, `/api/sessions/search`:

- direct ID matches and FTS matches are deduped by compression root
- result payload sets `payload["session_id"] = lineage_tip(root)`
- `lineage_tip()` calls `db.get_compression_tip(root)`

This means search result clicks can point at the latest tip even if the tip has zero messages. The content match itself can be in an older segment with messages, but the exposed `session_id` can be empty.

#### Tool session_search data flow

`tools/session_search_tool.py:226-266`, browse shape:

- calls `db.list_sessions_rich(limit=limit+5, exclude_sources=hidden, order_by_last_active=True)`
- filters out child rows by `parent_session_id`
- returns `session_id`, title, last_active, message_count, preview

Because projected rows can set `parent_session_id` to `None` while carrying a tip ID, browse can show empty tips as root-like rows.

`tools/session_search_tool.py:400-491`, discovery shape:

- FTS results use the raw owning `session_id` for anchored views
- dedupe is by lineage root
- this path is healthier for content search because it keeps `hit_sid` where the message actually exists

#### Resume mitigation

`hermes_state.py:2646-2709`, `resolve_resume_session_id()`:

- walks descendants and returns the first descendant with message rows
- docstring explicitly notes compression can create an empty parent/head and references issue `#15000`

This only helps when a descendant has messages. It does not solve the observed chain where the latest 11 continuation rows have no messages; it also does not help list/dashboard/search surfaces unless those callers use it or an equivalent message-bearing-tip resolver.

## Root-cause hypotheses

### H1 — primary: stale conversation_history skips all post-compression writes

Likelihood: high.

Mechanism:

1. Compression rotates `agent.session_id` and resets `_last_flushed_db_idx = 0`.
2. The finalizer or an error/overflow path calls `_persist_session(messages, conversation_history)` with old `conversation_history` still present.
3. `_flush_messages_to_session_db()` computes `flush_from = max(len(conversation_history), 0)`.
4. If `len(conversation_history) >= len(messages)`, no message rows are appended to the new child session.
5. API/token updates use the new `agent.session_id`, so usage accumulates against an empty session row.

Observed DB state matches all five steps exactly.

### H2 — list/dashboard min-message filtering is applied before compression-tip projection

Likelihood: confirmed by live call.

The SQL/list logic can select a root segment that has messages (`min_message_count=1` passes), then project that logical conversation to the latest continuation tip. If the latest tip is empty, the final returned row has `message_count=0` despite the caller requesting non-empty sessions.

### H3 — search exposes lineage tip even when the content hit is in an older segment

Likelihood: high from code path.

Dashboard `/api/sessions/search` sets returned `session_id` to `lineage_tip(root)`, not necessarily to the message-bearing `raw_sid`. This can make search results navigate to an empty tip even when FTS matched a real message in an older segment.

### H4 — resume/read repair handles only one direction

Likelihood: medium/high.

`resolve_resume_session_id()` searches descendants with messages. For a chain whose latest descendants are empty, the better user outcome is often nearest message-bearing ancestor/segment, not the empty tip. Existing code appears to preserve empty target if no descendant has messages.

### H5 — session source/profile mismatch is not the main cause in the inspected chain

Likelihood: high.

The problematic chain is consistently `source="cli"`, `model="gpt-5.5"`, and same root lineage. The issue is not caused by profile/source mismatch in this evidence set. Cross-profile list endpoints can still surface the same projection bug because they call the same `list_sessions_rich` logic.

## Phased improvement plan

### Phase 0 — Add diagnostics and invariants first

Purpose: make the failure mode easy to detect before changing behavior.

1. Add a diagnostic helper or CLI/dev command to report compression-chain integrity:
   - root id
   - tip id
   - segment count
   - zero-message segment count
   - zero-message-with-usage count
   - first/last message-bearing segment
   - last usage-bearing segment
2. Add log warning when a session has `api_call_count > 0` or tokens but zero message rows at end of turn.
3. Add a unit test fixture that creates a root -> child compression chain where root has messages, child has zero messages but usage counters.
4. Add assertions that `list_sessions_rich(min_message_count=1)` never returns a final projected row with `message_count=0` unless the caller explicitly asks for raw projection behavior.

Suggested tests:

- `tests/test_hermes_state.py::test_list_sessions_min_message_count_filters_after_projection`
- `tests/test_hermes_state.py::test_message_bearing_tip_prefers_latest_nonempty_segment`
- `tests/test_session_persistence.py::test_compression_flush_ignores_stale_conversation_history_after_rotation`

### Phase 1 — Fix list/search/dashboard surfaces so empty tips stop leaking

Purpose: immediate UX stabilization, low risk.

1. In `list_sessions_rich`, after compression-tip projection, re-apply `min_message_count` to the projected/final row.
2. Add a helper with explicit semantics, e.g.:

   ```python
   get_message_bearing_tip(root_id, prefer_latest=True)
   ```

   It should walk compression lineage and return the latest segment with `message_count > 0` / actual message rows. If no segment has messages, it can return the structural tip with a flag.

3. Use that helper when `min_message_count > 0` or when producing user-facing recents.
4. Keep current `get_compression_tip()` for routing/live-session semantics where the true active structural tip matters.
5. In `/api/sessions/search`, return both IDs:
   - `session_id`: message-bearing segment for opening/reading
   - `lineage_tip`: latest structural continuation
   - `lineage_root`: root
   - optionally `matched_session_id`: FTS owning segment
6. In session_search browse shape, avoid showing projected empty tips when preview/message_count is empty and a message-bearing segment exists.

Acceptance:

- Dashboard recents with `min_messages=1` never shows the `Workspace Cleanup and Archiving #40` empty tip as a non-empty row.
- Search for content from the root chain opens a segment with messages, not an empty tip.

### Phase 2 — Fix persistence across compression rotation

Purpose: stop creating new empty usage-bearing segments.

1. Modify `_flush_messages_to_session_db()` so stale `conversation_history` cannot skip a new session after compression. Options:

   Preferred minimal fix:

   ```python
   if self._last_flushed_db_idx == 0 and conversation_history:
       if len(conversation_history) > len(messages):
           start_idx = 0
       elif self._session_db and self.session_id:
           current_count = self._session_db.get_message_count(self.session_id)
           if current_count == 0 and self._parent_session_id_changed_or_session_rotated:
               start_idx = 0
   ```

   Cleaner fix:

   - Track the session id associated with the flush cursor, e.g. `_last_flushed_session_id`.
   - If `self.session_id != _last_flushed_session_id`, set `flush_from = 0` regardless of `conversation_history`.
   - After successful flush, set `_last_flushed_session_id = self.session_id` and `_last_flushed_db_idx = len(messages)`.

2. When compression rotates `agent.session_id`, explicitly record enough state for the flush layer:
   - `agent._last_flushed_session_id = agent.session_id` after child creation or set it to `None` before first child flush.
   - `agent._compression_rotated_session = True` until first successful flush.

3. Update all compression/error branches in `conversation_loop.py` and `turn_finalizer.py` so final persist cannot pass stale history after a rotation. The finalizer can defensively detect session rotation and pass `conversation_history=None`.

4. Add tests that simulate:
   - old history length greater than compressed messages length
   - `_last_flushed_db_idx=0`
   - new child `session_id`
   - `_persist_session(compressed_messages, old_conversation_history)`
   - expected: child gets compressed messages, not zero rows

Acceptance:

- New compression child rows with API usage also have message rows.
- No new `message_count=0 AND api_call_count>0` rows are produced in local manual smoke tests.

### Phase 3 — Repair or quarantine existing bad rows

Purpose: clean current state without destroying lineage.

Do not blindly delete rows. Preserve auditability.

1. Add a dry-run repair script first:
   - scan all profiles’ `state.db`
   - find `message_count=0 AND actual_messages=0 AND (api_call_count>0 OR tokens>0)`
   - group by compression root
   - identify nearest previous message-bearing segment
   - report proposed action

2. Safe default action:
   - mark empty usage-bearing continuation rows as archived/hidden from user-facing recents, or set a `model_config` marker such as `_empty_continuation=true`.
   - keep rows for token/accounting history.

3. Optional stronger repair only when source material exists:
   - If JSON session logs contain the compressed messages for the empty session, backfill them into `messages` with provenance marker.
   - If not, do not fabricate transcript content. Leave accounting rows as accounting-only lineage records.

4. Update dashboard/session_search to show a small integrity warning for rows that are accounting-only if someone opens them directly.

Acceptance:

- Current `Workspace Cleanup and Archiving #40` no longer appears as an empty primary recents row when non-empty sessions are requested.
- Existing token/accounting records remain available for audit.

### Phase 4 — Regression tests and observability

Purpose: make this stay fixed.

1. StateDB tests:
   - projection + min-message post-filter
   - message-bearing tip resolver
   - dashboard search result should include `matched_session_id` and not force empty tip
2. Agent persistence tests:
   - flush cursor is scoped by session id
   - stale `conversation_history` after compression cannot suppress child writes
   - token update + message flush target the same session id
3. TUI/dashboard API tests:
   - `/api/sessions?min_messages=1&order=recent` excludes projected empty tips
   - `/api/profiles/sessions` same behavior across read-only DBs
   - `/api/sessions/search` opens message-bearing result
4. Runtime observability:
   - log a single warning per affected session when usage exists but messages are zero
   - optionally expose DB integrity counts in dashboard diagnostics

### Phase 5 — Manual smoke validation

Run a focused smoke after implementation:

1. Start a local CLI/TUI session with tiny compression threshold in a temp Hermes home.
2. Force at least two compressions.
3. Run a tool call after each compression.
4. Query state.db:

   ```sql
   SELECT id, parent_session_id, message_count,
          (SELECT count(*) FROM messages m WHERE m.session_id=s.id) AS actual_messages,
          api_call_count, input_tokens, output_tokens, end_reason
   FROM sessions s
   ORDER BY started_at;
   ```

5. Assert:
   - every usage-bearing session has at least one persisted message row
   - dashboard recents show the latest usable segment
   - session_search browse/read/search returns message-bearing sessions
   - resume does not reopen an empty accounting-only row unless explicitly requested

## Recommended implementation order

1. Write failing tests for `list_sessions_rich(min_message_count=1)` projected empty tip.
2. Write failing test for `_flush_messages_to_session_db()` with stale `conversation_history` after session rotation.
3. Implement flush cursor session-id scoping.
4. Implement message-bearing-tip helper and post-projection filtering.
5. Patch dashboard search/session_search outputs to separate structural tip from message-bearing hit.
6. Add dry-run repair/audit script.
7. Run targeted tests, then broader session/dashboard test subset.

## Risk notes

- `get_compression_tip()` likely has valid live-routing uses. Do not globally change it to mean message-bearing tip. Add a new helper instead.
- Some rows with `message_count=0` are legitimate fresh sessions. Only classify as problematic when they have usage counters or are projected into non-empty user-facing lists.
- Existing empty rows may not be reconstructable. Do not invent/fabricate messages from summaries or token counts.
- Branch sessions and delegated child sessions also use `parent_session_id`; compression-edge detection must keep the current branch/delegate distinction.

## Open questions before implementation

1. Should dashboard opening a logical conversation default to the latest message-bearing segment or the latest structural/live tip with an inline warning?
2. Should accounting-only rows remain visible in an advanced diagnostics view?
3. Should existing JSON session logs be treated as authoritative enough for backfill, or only for manual recovery?
4. Should `resolve_resume_session_id()` change to prefer descendant-with-messages, then ancestor-with-messages, then raw target?
