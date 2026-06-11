---
sidebar_position: 9
title: "Context Engine Plugins"
description: "How to build a context engine plugin that replaces the built-in ContextCompressor"
---

# Building a Context Engine Plugin

Context engine plugins replace the built-in `ContextCompressor` with an alternative strategy for managing conversation context. For example, a Lossless Context Management (LCM) engine that builds a knowledge DAG instead of lossy summarization.

## How it works

The agent's context management is built on the `ContextEngine` ABC (`agent/context_engine.py`). The built-in `ContextCompressor` is the default implementation. Plugin engines must implement the same interface.

Only **one** context engine can be active at a time. Selection is config-driven:

```yaml
# config.yaml
context:
  engine: "compressor"    # default built-in
  engine: "lcm"           # activates a plugin engine named "lcm"
```

Plugin engines are **never auto-activated** — the user must explicitly set `context.engine` to the plugin's name.

## Directory structure

Each context engine lives in `plugins/context_engine/<name>/`:

```
plugins/context_engine/lcm/
├── __init__.py      # exports the ContextEngine subclass
├── plugin.yaml      # metadata (name, description, version)
└── ...              # any other modules your engine needs
```

## The ContextEngine ABC

Your engine must implement these **required** methods:

```python
from agent.context_engine import ContextEngine

class LCMEngine(ContextEngine):

    @property
    def name(self) -> str:
        """Short identifier, e.g. 'lcm'. Must match config.yaml value."""
        return "lcm"

    def update_from_response(self, usage: dict) -> None:
        """Called after every LLM call with the usage dict.

        Update self.last_prompt_tokens, self.last_completion_tokens,
        self.last_total_tokens from the response.
        """

    def should_compress(self, prompt_tokens: int = None) -> bool:
        """Return True if compaction should fire this turn."""

    def compress(self, messages: list, current_tokens: int = None,
                 focus_topic: str = None) -> list:
        """Compact the message list and return a new (possibly shorter) list.

        The returned list must be a valid OpenAI-format message sequence.

        ``focus_topic`` is an optional topic string from manual
        ``/compress <focus>``; engines that support guided compression should
        prioritise preserving information related to it, others may ignore it.
        """
```

### Class attributes your engine must maintain

The agent reads these directly for display and logging:

```python
last_prompt_tokens: int = 0
last_completion_tokens: int = 0
last_total_tokens: int = 0
threshold_tokens: int = 0        # when compression triggers
context_length: int = 0          # model's full context window
compression_count: int = 0       # how many times compress() has run
```

### Optional methods

These have sensible defaults in the ABC. Override as needed:

| Method | Default | Override when |
|--------|---------|--------------|
| `on_session_start(session_id, **kwargs)` | No-op | You need to load persisted state (DAG, DB). See [Compression boundaries](#compression-boundaries) for the kwargs the host sends on a compression split |
| `on_session_end(session_id, messages)` | No-op | You need to flush state, close connections |
| `on_session_reset()` | Resets token counters | You have per-session state to clear |
| `update_model(model, context_length, ...)` | Updates context_length + threshold | You need to recalculate budgets on model switch |
| `get_tool_schemas()` | Returns `[]` | Your engine provides agent-callable tools (e.g., `lcm_grep`) |
| `handle_tool_call(name, args, **kwargs)` | Returns error JSON | You implement tool handlers |
| `should_compress_preflight(messages)` | Returns `False` | You can do a cheap pre-API-call estimate |
| `get_status()` | Standard token/threshold dict | You have custom metrics to expose |

## Engine tools

Context engines can expose tools the agent calls directly. Return schemas from `get_tool_schemas()` and handle calls in `handle_tool_call()`:

```python
def get_tool_schemas(self):
    return [{
        "name": "lcm_grep",
        "description": "Search the context knowledge graph",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"],
        },
    }]

def handle_tool_call(self, name, args, **kwargs):
    if name == "lcm_grep":
        results = self._search_dag(args["query"])
        return json.dumps({"results": results})
    return json.dumps({"error": f"Unknown tool: {name}"})
```

Engine tools are injected into the agent's tool list at startup and dispatched automatically — no registry registration needed.

## Registration

### Via directory (recommended)

Place your engine in `plugins/context_engine/<name>/`. The `__init__.py` must export a `ContextEngine` subclass. The discovery system finds and instantiates it automatically.

### Via general plugin system

A general plugin can also register a context engine:

```python
def register(ctx):
    engine = LCMEngine(context_length=200000)
    ctx.register_context_engine(engine)
```

Only one engine can be registered. A second plugin attempting to register is rejected with a warning.

## Lifecycle

```
1. Engine instantiated (plugin load or directory discovery)
2. on_session_start() — conversation begins
3. update_from_response() — after each API call
4. should_compress() — checked each turn
5. compress() — called when should_compress() returns True
6. on_session_end() — session boundary (CLI exit, /reset, gateway expiry)
```

`on_session_reset()` is called on `/new` or `/reset` to clear per-session state without a full shutdown.

### Compression boundaries

When a session store is active, a successful `compress()` does not just shrink
the in-memory list — the host also **rotates the session id**: the old session
row is ended with `end_reason="compression"` and a new continuation row is
created with `parent_session_id` pointing at the old one. The host then
re-fires `on_session_start` with kwargs that let your engine preserve
continuity across the split instead of treating it as a fresh `/new`:

```python
on_session_start(
    new_session_id,
    boundary_reason="compression",
    old_session_id=old_session_id,
    conversation_id=...,   # gateway session key, when one exists
)
```

Your `on_session_start` MUST accept `**kwargs` (as the ABC signature shows):
the host passes additional kwargs here and at init time (`hermes_home`,
`platform`, `model`, `context_length`, `conversation_id`), and a strict
signature raises a `TypeError` that the host swallows — your engine would
silently miss the notification.

Engines that key persisted state (a DAG, a DB) to the session id should treat
a `boundary_reason="compression"` call as a *continuation* of
`old_session_id`, not a new conversation (this contract exists because an
early LCM build lost its DAG lineage at every compaction). An engine that
raises from this hook does not break compression — the host swallows the
error and continues.

Host persistence guarantees at the boundary (pinned by
`tests/test_session_stability_compression.py` and
`tests/run_agent/test_compression_boundary_hook.py`):

- The message list returned by `compress()` is persisted to the **new
  continuation session row**, even if a stale pre-compression history is
  still floating around in a finalizer (the DB flush cursor is scoped by
  session id).
- The rotation bookkeeping is applied immediately after the id swap, before
  any fallible session-DB call that follows it (continuation-row creation,
  title propagation), so a transient SQLite failure mid-split cannot strand
  the continuation row as an accounting-only record (token counters but
  zero messages). A failure *before* the swap aborts the whole split — no
  rotation happens at all.
- Engines cannot rotate the session id themselves — no host→engine call
  ever passes the agent object or the session-DB handle (engines see only
  plain data: ids, messages, usage, model/config metadata), so all engines
  (built-in and plugin) share the same hardened rotation path.

## Configuration

Users select your engine via `hermes plugins` → Provider Plugins → Context Engine, or by editing `config.yaml`:

```yaml
context:
  engine: "lcm"   # must match your engine's name property
```

The `compression` config block (`compression.threshold`, `compression.protect_last_n`, etc.) is specific to the built-in `ContextCompressor`. Your engine should define its own config format if needed, reading from `config.yaml` during initialization.

## Testing

```python
from agent.context_engine import ContextEngine

def test_engine_satisfies_abc():
    engine = YourEngine(context_length=200000)
    assert isinstance(engine, ContextEngine)
    assert engine.name == "your-name"

def test_compress_returns_valid_messages():
    engine = YourEngine(context_length=200000)
    msgs = [{"role": "user", "content": "hello"}]
    result = engine.compress(msgs)
    assert isinstance(result, list)
    assert all("role" in m for m in result)
```

See `tests/agent/test_context_engine.py` for the full ABC contract test suite.

## See also

- [Context Compression and Caching](/developer-guide/context-compression-and-caching) — how the built-in compressor works
- [Memory Provider Plugins](/developer-guide/memory-provider-plugin) — analogous single-select plugin system for memory
- [Plugins](/user-guide/features/plugins) — general plugin system overview
