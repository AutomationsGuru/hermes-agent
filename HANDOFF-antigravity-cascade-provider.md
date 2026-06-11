# HANDOFF — Antigravity Cascade OAuth Provider for Hermes

Generated: 2026-06-10 by Hermes (Mother/default), for Claude Code Desktop.
Worktree: `C:\Users\RDP\Projects\hermes-agent-cascade-probe`
Branch: `mother/antigravity-cascade-probe` (based on ff9c110d5)
Main checkout (DO NOT EDIT): `C:\Users\RDP\.hermes\hermes-agent` (main) — the live Hermes install. All work happens in this probe worktree only.

Mission: make Google Antigravity (the local Antigravity IDE language server, OAuth-backed) a first-class Hermes model provider route, without API keys, by speaking the Cascade protocol to the local language server.

---

## HERE — current verified state (all live-tested 2026-06-10)

Slices 1–3 are DONE, uncommitted in this worktree.

### What works right now (live evidence, not just unit tests)

1. OAuth-free provider route through the existing `google-gemini-cli` adapter:
   - `antigravity-cascade` -> returns `'OK'`, finish_reason=stop (slice 2 acceptance PASS)
   - `antigravity-claude-sonnet-4-6` -> `'NAMESPACED_OK'` (cross-vendor preset via local Cascade)
   - `gemini-3.5-flash-low` -> `'ALIAS_OK'` (app alias -> internal MODEL_PLACEHOLDER enum)
   - The route never calls Google OAuth token refresh or Code Assist project context (tested + enforced).
2. Rich event parsing (slice 3): Cascade workspace/tool steps (e.g. `CORTEX_STEP_TYPE_LIST_DIRECTORY`) normalize to `tool_call`/`tool_result` events instead of being flattened to `done`. Live workspace probe returned tool_result events + correct file listing.
3. Sanitized live probe with `--workspace-uri` support and summary counts (assistant_text/done/error/tool_call/tool_result/unknown).
4. Antigravity endpoint discovery: port + CSRF token recovered from Antigravity's `main.log` (last URL wins; stale ports skipped). Currently healthy at `https://127.0.0.1:<dynamic-port>` (was 51520 this session; port changes per Antigravity restart — always rediscover).

### Files in play (uncommitted; `git status` is the source of truth)

Modified:
- `agent/gemini_cloudcode_adapter.py` — Antigravity routing branch in `_create_chat_completion`, `_create_antigravity_cascade_completion`, prompt flattening, capability gates, OpenAI-shape response builder
- `hermes_cli/models.py` — curated `google-gemini-cli` list now includes app aliases + `antigravity-cascade`
- `tests/agent/test_gemini_cloudcode.py` — route tests, alias tests, regression guards

New:
- `agent/gemini_cloudcode_models.py` — picker alias catalog (picker_id -> backend enum, route hint via private `extra_body` key, capability metadata, slug resolution)
- `agent/google_antigravity_bridge.py` — endpoint discovery (main.log scan, CSRF extraction, availability check)
- `agent/google_antigravity_cascade.py` — Cascade client + event parser + redaction (998 lines)
- `scripts/probe_antigravity_cascade.py` — sanitized live probe CLI
- `tests/agent/test_google_antigravity_bridge.py`, `tests/agent/test_google_antigravity_cascade.py`, `tests/scripts/test_probe_antigravity_cascade.py`
- `tests/fixtures/antigravity/*.json` — sanitized event fixtures
- `.agents/plans/2026-06-09_*.md` — the three slice packets (read these for full protocol detail)

### Protocol facts (live-proven, do not rediscover by dumping raw logs)

- `StartCascade` — POST `/exa.language_server_pb.LanguageServerService/StartCascade`; payload: `requested_model` (e.g. `MODEL_PLACEHOLDER_M132`), `source=CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT`, `trajectory_type=CORTEX_TRAJECTORY_TYPE_CASCADE`, safe metadata. Antigravity returns the cascade id — never preset one.
- `SendUserCascadeMessage` — payload needs `cascade_id`, `items:[{text}]`, and `cascade_config.planner_config.plan_model` or live errors with "neither PlanModel nor RequestedModel specified".
- `StreamAgentStateUpdates` — Connect streaming with 5-byte envelopes (NOT plain JSON); request uses `conversation_id` (not cascade_id); text nests under `plannerResponse.modifiedResponse`.
- Auth: header `x-codeium-csrf-token` extracted from the app config HTML; base URL from Antigravity main.log.
- Model enums confirmed: M132 (3.5 Flash High), M187 (Low), M20 (Medium), M36/M16 (3.1 Pro Low/High), M50 (3.1 Flash Lite), M35 (Claude Sonnet 4.6 Thinking), M26 (Claude Opus 4.6 Thinking), MODEL_OPENAI_GPT_OSS_120B_MEDIUM.
- Richer RPCs exist on the same service for the next slices: `GetCascadeTrajectorySteps`, `WaitForConversationFullyIdle`, `StreamCascadeReactiveUpdates`, `RunCommand`, `ReadFile`/`WriteFile`, `GetTurnDiff`, `HandleCascadeUserInteraction`, `AcknowledgeCascadeCodeEdit`, MCP management RPCs.

### Deliberate capability gates (slice 2/3 scope decisions)

- `tools`/forcing `tool_choice` -> raise `CodeAssistError` code `antigravity_cascade_tools_unsupported`
- `stream=True` -> raise code `antigravity_cascade_stream_unsupported`
- Parsed Cascade tool events are observation-only: nothing executes, no edits acknowledged.

### Fix applied this session (keep the guard tests green)

Cross-vendor picker IDs were namespaced (`antigravity-claude-sonnet-4-6`, `antigravity-claude-opus-4-6-thinking`, `antigravity-gpt-oss-120b-medium`) because the bare names collided with the `anthropic`/`opencode-zen` catalogs and shadowed the `sonnet`/`opus`/`gpt` short aliases in `detect_provider_for_model`. Raw app slugs still resolve via the alias map. Regression guards: `test_google_gemini_cli_curated_ids_do_not_shadow_other_catalogs`, `test_cross_vendor_picker_ids_still_resolve_via_app_slug`.

### Test status

- Touched-surface suite: 385 passed, 1 skipped. ruff (0.15.16) clean.
- Full `tests/hermes_cli/` sweep: 6129 passed; 286 failures are PRE-EXISTING batch-pollution (reproduce on clean tree with changes stashed, pass standalone; domains: webhook CLI, web UI build, stale dashboard, uv tool update, oauth dispatch, pty import). Not caused by this work — do not chase them in this lane.
- Known flake: `tests/hermes_cli/test_active_sessions.py::test_cross_process_acquire_claims_only_one_last_slot` (Windows cross-process lock; fails on clean tree too).

---

## THERE — the goal

`google-gemini-cli` (Antigravity route) usable as a normal day-to-day Hermes provider:

1. Hermes agent loop can select an Antigravity model and hold a conversation (already true for plain text).
2. Hermes tool calling works through the route — either (a) Cascade acts as a pure LLM and Hermes-side tools round-trip through it, or (b) Cascade's own agent steps map to gated Hermes-visible events with explicit approval before any execution/edit. Slice packets lean (b)-observed + (a)-investigate; the architecture decision between them is RESERVED FOR MATTHEW — present the trade-off, don't pick silently.
3. Streaming responses (Connect envelope stream -> OpenAI-style chunks via the adapter's existing `_make_stream_chunk` machinery).
4. Graceful unavailability: when Antigravity isn't running, model listing/selection degrades with a clear actionable error (launch Antigravity), never a stack trace, and never blocks the rest of the provider.
5. Merge routing: changes flow from this probe worktree into the hermes-agent fork (origin AutomationsGuru/claude-code-style fork; upstream NousResearch) via Matthew-approved PR. NOTHING merges or lands in the live install without his explicit go.

---

## PATH — what to build, in order

### Slice 4 — streaming support (smallest valuable step)
- Implement `stream=True` for Antigravity models in `_create_antigravity_cascade_completion`: translate parsed `assistant_text` deltas into OpenAI-style chunks (reuse `_make_stream_chunk` / `_stream_completion` shapes already in the adapter).
- Handle the merge/dedup problem: Cascade re-emits full text in generating + done states; stream only suffix deltas (the `_merge_antigravity_assistant_text` helper shows the dedup rules).
- Tests: fake client streaming fixtures; assert chunk sequence, final usage/finish_reason, no duplicated text.

### Slice 5 — tool semantics decision + implementation
- Write a short HERE/THERE/PATH options memo for Matthew: (a) pure-LLM round-trip vs (b) Cascade-agent event mapping with approval gates. Include what `GetCascadeTrajectorySteps` + `HandleCascadeUserInteraction` offer for (b).
- Implement whichever Matthew picks. Hard rules either way: no auto-execution, no `AcknowledgeCascadeCodeEdit` without explicit approval flow, redaction preserved.

### Slice 6 — provider/runtime integration polish
- `hermes_cli/runtime_provider.py` + `hermes_cli/auth.py`: make sure the Antigravity route reports availability honestly in `hermes status` / model picker (it must not claim ready when the language server is down; bridge has `check_antigravity_available`).
- Availability-aware model picker: hide or annotate antigravity-* entries when the local server is unreachable.
- Decide/confirm capability metadata flow (`google_gemini_cli_model_capabilities` exists in `agent/gemini_cloudcode_models.py` but nothing consumes it yet — wire it or delete it).

### Slice 7 — merge prep
- Squash/organize into reviewable commits on `mother/antigravity-cascade-probe` (ONLY after Matthew says commit).
- Run the canonical suite via `scripts/run_tests.sh` equivalents on touched dirs; re-run ruff; write PR description with live evidence transcript.

---

## Working rules for this lane (binding)

1. Stay inside `C:\Users\RDP\Projects\hermes-agent-cascade-probe`. Never edit `C:\Users\RDP\.hermes\hermes-agent` (live install) or `~/.hermes` config/auth stores.
2. Do not commit or push until Matthew explicitly approves.
3. Never print secrets, CSRF tokens, OAuth tokens, raw headers, or raw payloads. Use/extend the existing sanitizers (`sanitize_console_text`, `sanitize_cascade_payload`).
4. Do not restart/kill Antigravity, the 9119 dashboard, gateway, Camofox, or any Agent OS service. If Antigravity is down, report it and stop.
5. Do not auto-execute Cascade tool steps or acknowledge code edits.
6. Architecture decisions (tool semantics, provider naming, merge strategy) -> present options to Matthew with evidence; he decides.
7. Report format for each slice: STATUS / FILES_CHANGED / TESTS_RUN / LIVE_PROBE / NOTES.

## Verification recipes (copy/paste, Git Bash/MSYS)

Focused tests (the repo addopts force a 30s signal timeout that breaks on Windows — always override):

    cd /c/Users/RDP/Projects/hermes-agent-cascade-probe
    export PYTHONPATH="$(pwd)"
    python -m pytest tests/agent/test_gemini_cloudcode.py tests/agent/test_google_antigravity_bridge.py tests/agent/test_google_antigravity_cascade.py tests/scripts/test_probe_antigravity_cascade.py tests/hermes_cli/test_models.py -q -o 'addopts=' -p no:timeout

Lint:

    uvx ruff@0.15.16 check agent/gemini_cloudcode_models.py agent/google_antigravity_cascade.py agent/google_antigravity_bridge.py agent/gemini_cloudcode_adapter.py scripts/probe_antigravity_cascade.py hermes_cli/models.py

Live smoke (sanitized; requires Antigravity desktop app running):

    python scripts/probe_antigravity_cascade.py --prompt 'Return exactly OK.' --max-events 100 --timeout 60

Live provider acceptance:

    python -c "
    from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient
    c = GeminiCloudCodeClient()
    r = c.chat.completions.create(model='antigravity-cascade', messages=[{'role':'user','content':'Return exactly OK.'}])
    print(repr(r.choices[0].message.content), r.choices[0].finish_reason)
    "

## Windows/MSYS landmines already hit

- Repo `pyproject.toml` addopts use `--timeout-method=signal` -> hangs/erratic on Windows; always pass `-q -o 'addopts=' -p no:timeout`.
- Hermes terminal tool rejects heredocs containing `&`/backgrounding patterns; for Claude Code Desktop normal heredocs are fine, but prefer script files for multi-line python.
- `python3` missing; use `python` (3.11). Fallback runner: `"/c/Program Files/Python311/python"`.
- ruff is not installed in the venv; use `uvx ruff@0.15.16`.
- Antigravity port is dynamic per restart; the bridge rediscovers from main.log — never hardcode.
- `LOCAL_AGENTS.md` at repo root is from the Wave-4 DOX distribution (separate workstream) — leave it alone. Root `AGENTS.md` is upstream-governed — do not hand-edit.

## First message to anchor this lane

Read C:\Users\RDP\Projects\hermes-agent-cascade-probe\HANDOFF-antigravity-cascade-provider.md and the three packets under .agents/plans/, run the focused test + lint recipes from the handoff to confirm the baseline is green, then propose a Slice 4 (streaming) implementation plan for approval. Do not commit, push, or touch the live Hermes install.
