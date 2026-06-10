# Antigravity provider route (google-gemini-cli)

Hermes can use Google **Antigravity**'s local, OAuth-backed IDE language server as a
model provider — **no API key** — by speaking the Cascade protocol to the local server.
This unlocks the cross-vendor models the Antigravity subscription includes (Claude, GPT)
alongside Gemini, usable from Hermes itself.

It rides the existing `google-gemini-cli` OpenAI-compatible adapter but **bypasses Google
OAuth / Code Assist** for Antigravity models. Requires the Antigravity desktop app to be
running; the endpoint + CSRF token are rediscovered from Antigravity's `main.log`
(dynamic port — never hardcoded).

## Models & capabilities

| Picker ID | Vendor | Chat | Tool calling |
|---|---|---|---|
| `antigravity-claude-sonnet-4-6` | Anthropic | ✅ | ❌ chat-only |
| `antigravity-claude-opus-4-6-thinking` | Anthropic | ✅ | ❌ chat-only |
| `antigravity-gpt-oss-120b-medium` | OpenAI | ✅ | ✅ |
| `gemini-3.5-flash-*`, `gemini-3.1-pro-*`, `antigravity-cascade` | Google | ✅ | ❌ (use the normal `gemini`/`google-gemini-cli` cloudcode route for Gemini tools) |

**Why Claude is chat-only:** tool calling here is **Option A** — Hermes renders tool
definitions into a system preamble, the model replies with a strict-JSON tool call, and we
parse it back (Hermes owns the tool loop; nothing executes inside Antigravity). Live
verification (2026-06-10) showed the Claude models, wrapped in Antigravity's own agent
persona, do **not** reliably emit prompt-injected tool calls (Opus answers without calling
even when forced; Sonnet deliberates ~60s then truncates). GPT-OSS does, reliably (~2–3s).
So tools are gated to GPT-OSS; Claude/Opus remain excellent chat models. Cross-vendor
chat (and streaming) was verified working for all three.

## Maintenance

- **Single source of truth for tool support:** `ANTIGRAVITY_TOOL_ENABLED_ENUMS` in
  [`agent/gemini_cloudcode_models.py`](../agent/gemini_cloudcode_models.py). The adapter's
  runtime tool gate (`agent/gemini_cloudcode_adapter.py`) AND
  `google_gemini_cli_model_capabilities()` both read this exact frozenset, so the advertised
  capability can never drift from what is enforced (a test asserts they're the same object).
  **To enable tools on another model, add its backend enum here — but only after live
  re-verification** that it reliably emits tool calls over Cascade.
- **Chat-only marker:** `google_gemini_cli_model_capabilities(id)["chat_only"]` is the flag;
  the CLI picker appends "(chat-only, no tools)" via `_antigravity_chat_only_suffix()` in
  `hermes_cli/auth.py`, gated to `antigravity-` IDs so other providers' catalogs aren't
  mis-marked. Web/TUI picker payloads carry the same flag via `hermes_cli/inventory.py`
  (frontend badge rendering lives in the built `tui_dist`/`web_dist` assets).
- **Tool calling internals (Option A):** preamble rendering, tolerant multi-candidate JSON
  parsing, tool-result round-trip, a synthesis-mode preamble (so the model answers from a
  tool result instead of re-calling), repair retry, and final-full-snapshot collection
  (avoids re-emission corruption) all live in `agent/gemini_cloudcode_adapter.py`.
- **Availability:** when Antigravity is down, antigravity-route models are hidden from the
  picker with an actionable message; cloudcode-route models are unaffected
  (`filter_unavailable_google_gemini_cli_models` in `hermes_cli/models.py`). The probe is
  local-only, memoized, and never raises.

## Future: Claude tool calling

Not achievable via Option A (above). Open avenues, reserved for a maintainer decision:
parse Antigravity's **native** tool-call syntax instead of injecting JSON, or **Option B**
(drive Cascade's own agent tools with explicit approval gates). Both are larger and carry
execution/approval (and async-subagent) risks — see the slice-5 tool-semantics analysis.

## Probes

- `scripts/probe_antigravity_cascade.py` — sanitized Cascade stream probe.
- `scripts/probe_antigravity_models.py` — read-only roster discovery (`GetAvailableModels`).
