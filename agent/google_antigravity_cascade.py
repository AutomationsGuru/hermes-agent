"""Safe Antigravity Cascade protocol exploration helpers.

This module intentionally stops at sanitized probe/parser primitives.  It does
not integrate Cascade with Hermes provider routing, model selection, tools, or
gateway surfaces.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

import httpx

from agent.google_antigravity_bridge import (
    AntigravityBridgeError,
    AntigravityEndpoint,
    discover_antigravity_endpoint,
)


CascadeEventType = Literal[
    "assistant_text",
    "tool_call",
    "tool_result",
    "done",
    "error",
    "waiting",
    "unknown",
]


class AntigravityCascadeError(RuntimeError):
    """Raised when Antigravity Cascade probing fails safely."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(
            sanitize_console_text(message, max_chars=_DEFAULT_STORED_TEXT_LIMIT)
        )
        self.status_code = status_code
        self.code = "antigravity_cascade_error"


@dataclass(frozen=True)
class AntigravityCascadeSession:
    """Safe, token-free Cascade session handle."""

    cascade_id: str
    base_url: str = ""
    http_status: int | None = None


@dataclass(frozen=True)
class AntigravityCascadeEvent:
    """Normalized, sanitized Cascade observation.

    ``conversation_done`` distinguishes the conversation-level completion
    signal (top-level ``status=CASCADE_RUN_STATUS_IDLE`` in a state update)
    from per-step completions, which also normalize to ``type="done"`` /
    ``type="tool_result"`` but never terminate the conversation.
    """

    type: CascadeEventType
    text: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    raw_kind: str = ""
    conversation_done: bool = False


_REDACTED = "[REDACTED]"
_REDACTED_HEADERS = "[REDACTED_HEADERS]"
_SERVICE_PREFIX = "/exa.language_server_pb.LanguageServerService/"
_DEFAULT_STORED_TEXT_LIMIT = 500
_DEFAULT_CONSOLE_TEXT_LIMIT = 120
_CONNECT_JSON_CONTENT_TYPE = "application/connect+json"

_SENSITIVE_KEY_COMPACT = {
    "authorization",
    "cookie",
    "xcodeiumcsrftoken",
    "csrf",
    "csrftoken",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "apikey",
    "password",
    "secret",
}
_HEADER_KEY_COMPACT = {
    "headers",
    "rawheaders",
    "requestheaders",
    "responseheaders",
    "httpheaders",
}
_INLINE_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(authorization|cookie|x-codeium-csrf-token|access_token|"
        r"refresh_token|id_token|api[_-]?key|password|secret|csrfToken|csrf)\b"
        r"\s*[:=]\s*['\"]?[^\s,'\"}]+"
    ),
)

_LABEL_KEYS = (
    "type",
    "kind",
    "event",
    "eventType",
    "event_type",
    "stepType",
    "step_type",
    "name",
    "role",
    "status",
    "state",
    "phase",
)
_TEXT_KEYS = (
    "text",
    "content",
    "message",
    "response",
    "output",
    "result",
    "summary",
)
_TOOL_NAME_KEYS = (
    "toolName",
    "tool_name",
    "functionName",
    "function_name",
    "serverName",
    "server_name",
    "name",
)
_TOOL_ARG_KEYS = (
    "args",
    "arguments",
    "parameters",
    "input",
    "toolArgs",
    "tool_args",
)
_NESTED_CONTAINER_KEYS = (
    "items",
    "messages",
    "updates",
    "events",
    "agentState",
    "agent_state",
    "cascade",
    "trajectory",
    "data",
    "payload",
)


def _compact_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


_DIRECT_EVENT_KEY_COMPACT = {
    *{_compact_key(key) for key in _LABEL_KEYS},
    *{_compact_key(key) for key in _TEXT_KEYS},
    *{_compact_key(key) for key in _TOOL_NAME_KEYS},
    *{_compact_key(key) for key in _TOOL_ARG_KEYS},
    "done",
    "complete",
    "completed",
    "finished",
    "terminal",
    "finishreason",
    "error",
    "exception",
    "failure",
    "toolcall",
    "toolcalls",
    "toolresult",
    "toolresults",
    "functioncall",
    "mcpserver",
}
_CORTEX_NON_OPERATION_MARKERS = (
    "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
    "CORTEX_STEP_TYPE_USER_INPUT",
    "CORTEX_STEP_TYPE_CONVERSATION_HISTORY",
    "CORTEX_STEP_TYPE_CHECKPOINT",
)
_CORTEX_OPERATION_TOOL_NAMES = (
    (
        ("CORTEX_STEP_TYPE_LIST_DIRECTORY", "list_directory", "list directory"),
        "list_dir",
    ),
    (("CORTEX_STEP_TYPE_READ_FILE", "read_file", "read file"), "read_file"),
    (("CORTEX_STEP_TYPE_SEARCH_CODE", "search_code", "search code"), "search_code"),
    (("CORTEX_STEP_TYPE_SEARCH_FILES", "search_files", "search files"), "search_files"),
    (
        (
            "CORTEX_STEP_TYPE_RUN_COMMAND",
            "terminal_shell_command",
            "shell_command",
            "run_command",
            "run command",
        ),
        "run_command",
    ),
    (("CORTEX_STEP_TYPE_WRITE_FILE", "write_file", "write file"), "write_file"),
    (("code_action", "code_edit", "code edit"), "code_edit"),
)
_CORTEX_OPERATION_RESULT_KEYS = ("result", "output", "observation")

# Conversation-level run status (live-proven 2026-06-10): every
# StreamAgentStateUpdates message carries a top-level ``update.status`` using
# the CASCADE_RUN_STATUS_* enum namespace (distinct from per-step
# CORTEX_STEP_STATUS_*).  It is RUNNING while the planner generates and flips
# to IDLE once the conversation is fully idle.  ``executableStatus`` is IDLE
# even mid-run, so only the plain ``status`` key is trusted here.
_CASCADE_RUN_STATUS_PREFIX = "CASCADE_RUN_STATUS"
_CASCADE_RUN_IDLE_STATUSES = {"CASCADE_RUN_STATUS_IDLE"}


def _is_sensitive_key(key: Any) -> bool:
    return _compact_key(key) in _SENSITIVE_KEY_COMPACT


def _is_header_key(key: Any) -> bool:
    return _compact_key(key) in _HEADER_KEY_COMPACT


def _redact_inline_secrets(text: str) -> str:
    redacted = text
    redacted = _INLINE_SECRET_PATTERNS[0].sub(
        lambda match: f"{match.group(1)} {_REDACTED}", redacted
    )
    redacted = _INLINE_SECRET_PATTERNS[1].sub(
        lambda match: f"{match.group(1)}={_REDACTED}", redacted
    )
    return redacted


def truncate_cascade_text(
    value: Any,
    *,
    max_chars: int = _DEFAULT_STORED_TEXT_LIMIT,
) -> str:
    """Return a redacted, bounded string representation."""

    if value is None:
        return ""
    text = _redact_inline_secrets(str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if max_chars >= 0 and len(text) > max_chars:
        return f"{text[:max_chars]}...[truncated]"
    return text


def sanitize_cascade_payload(
    value: Any,
    *,
    max_string: int = _DEFAULT_STORED_TEXT_LIMIT,
    max_string_length: int | None = None,
) -> Any:
    """Recursively redact sensitive fields and bound string values."""

    if max_string_length is not None:
        max_string = max_string_length
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = str(key)
            if _is_header_key(safe_key):
                sanitized[safe_key] = _REDACTED_HEADERS
            elif _is_sensitive_key(safe_key):
                sanitized[safe_key] = _REDACTED
            else:
                sanitized[safe_key] = sanitize_cascade_payload(
                    item, max_string=max_string
                )
        return sanitized
    if isinstance(value, list | tuple):
        return [sanitize_cascade_payload(item, max_string=max_string) for item in value]
    if isinstance(value, bytes | bytearray):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        return truncate_cascade_text(value, max_chars=max_string)
    # JSON scalars (bool/int/float/None) pass through unchanged; any other,
    # non-JSON type (set, custom object) is stringified-and-redacted so the
    # recursive sanitizer can never return an unscanned container verbatim.
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return truncate_cascade_text(value, max_chars=max_string)


def sanitize_console_text(
    value: Any,
    *,
    max_chars: int = _DEFAULT_CONSOLE_TEXT_LIMIT,
) -> str:
    """Return one-line sanitized text for terminal output."""

    text = truncate_cascade_text(value, max_chars=max_chars)
    return " ".join(text.split())


def parse_cascade_events(
    payload: dict[str, Any] | list[Any] | str | None,
) -> list[AntigravityCascadeEvent]:
    """Normalize plausible Cascade stream/update payloads into safe events."""

    decoded = _maybe_decode_json_string(payload)
    events = _parse_node(decoded, depth=0)
    if events:
        return events
    return [_unknown_event(decoded)]


def _maybe_decode_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except ValueError:
        return value


def _parse_node(value: Any, *, depth: int) -> list[AntigravityCascadeEvent]:
    if depth > 12:
        return []
    value = _maybe_decode_json_string(value)
    if isinstance(value, list | tuple):
        events: list[AntigravityCascadeEvent] = []
        for item in value:
            events.extend(_parse_node(item, depth=depth + 1))
        return events
    if not isinstance(value, dict):
        return []

    event = _event_from_mapping(value)
    events = [event] if event is not None else []
    nested_keys = _NESTED_CONTAINER_KEYS if event is not None else tuple(value.keys())
    for key in nested_keys:
        if _is_sensitive_key(key) or _is_header_key(key):
            continue
        item = value.get(key)
        if isinstance(item, dict | list | tuple | str):
            events.extend(_parse_node(item, depth=depth + 1))
    conversation_done_event = _conversation_done_event(value)
    if conversation_done_event is not None:
        # Append last so assistant text carried by the same state update is
        # merged before consumers break on the conversation-level signal.
        events.append(conversation_done_event)
    return events


def _conversation_run_status(value: dict[str, Any]) -> str:
    """Return the mapping's own top-level CASCADE_RUN_STATUS_* value, if any."""

    for key, item in value.items():
        if _compact_key(key) != "status" or not isinstance(item, str):
            continue
        text = item.strip()
        if text.upper().startswith(_CASCADE_RUN_STATUS_PREFIX):
            return text
    return ""


def _conversation_done_event(
    value: dict[str, Any],
) -> AntigravityCascadeEvent | None:
    status = _conversation_run_status(value)
    if status.upper() not in _CASCADE_RUN_IDLE_STATUSES:
        return None
    safe_status = sanitize_console_text(status)
    return AntigravityCascadeEvent(
        type="done",
        text=safe_status,
        raw_kind=safe_status,
        conversation_done=True,
    )


def _event_from_mapping(value: dict[str, Any]) -> AntigravityCascadeEvent | None:
    if not _has_direct_event_signal(value):
        return None
    labels = _labels_for(value)
    raw_kind = _raw_kind(value)
    if _is_error_event(value, labels):
        return AntigravityCascadeEvent(
            type="error",
            text=_extract_error_text(value),
            raw_kind=raw_kind,
        )
    planner_text = _extract_planner_response_text(value)
    if planner_text:
        return AntigravityCascadeEvent(
            type="assistant_text",
            text=planner_text,
            raw_kind=raw_kind,
        )
    if _is_cortex_operation_event(value, labels):
        event_type: CascadeEventType = (
            "tool_result"
            if _is_cortex_operation_completion(value, labels)
            else "tool_call"
        )
        return AntigravityCascadeEvent(
            type=event_type,
            text=_extract_text(value) or _extract_done_text(value),
            tool_name=_tool_name_from_cortex_step(value, labels),
            tool_args=_extract_tool_args(value),
            raw_kind=raw_kind,
        )
    if _is_done_event(value, labels):
        return AntigravityCascadeEvent(
            type="done",
            text=_extract_done_text(value),
            raw_kind=raw_kind,
        )
    if _is_tool_event(value, labels):
        event_type: CascadeEventType = "tool_call"
        label_text = " ".join(labels)
        if any(
            marker in label_text
            for marker in ("result", "response", "output", "observation")
        ):
            event_type = "tool_result"
        return AntigravityCascadeEvent(
            type=event_type,
            text=_extract_text(value),
            tool_name=_extract_tool_name(value),
            tool_args=_extract_tool_args(value),
            raw_kind=raw_kind,
        )
    if _is_waiting_event(labels):
        return AntigravityCascadeEvent(
            type="waiting",
            text=_extract_text(value),
            raw_kind=raw_kind,
        )
    if _is_assistant_text_event(value, labels):
        return AntigravityCascadeEvent(
            type="assistant_text",
            text=_extract_text(value),
            raw_kind=raw_kind,
        )
    return None


def _has_direct_event_signal(value: dict[str, Any]) -> bool:
    return any(_compact_key(key) in _DIRECT_EVENT_KEY_COMPACT for key in value)


def _labels_for(value: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in _LABEL_KEYS:
        found = _find_first_value(value, key)
        if isinstance(found, str | int | float | bool):
            labels.append(str(found).strip().lower())
    return labels


def _labels_contain(labels: list[str], *markers: str) -> bool:
    label_text = " ".join(labels).lower()
    compact_label_text = _compact_key(label_text)
    return any(
        marker.lower() in label_text or _compact_key(marker) in compact_label_text
        for marker in markers
    )


def _raw_kind(value: dict[str, Any]) -> str:
    for key in _LABEL_KEYS:
        found = _find_first_value(value, key)
        if isinstance(found, str | int | float | bool):
            return sanitize_console_text(found)
    return ""


def _is_error_event(value: dict[str, Any], labels: list[str]) -> bool:
    label_text = " ".join(labels)
    if any(
        marker in label_text for marker in ("error", "exception", "failed", "failure")
    ):
        return True
    for key, item in value.items():
        compact = _compact_key(key)
        if compact in {"error", "exception", "failure"} and item:
            return True
        if compact == "status" and str(item).lower() in {"error", "failed", "failure"}:
            return True
    return False


def _is_cortex_operation_event(value: dict[str, Any], labels: list[str]) -> bool:
    if _labels_contain(labels, *_CORTEX_NON_OPERATION_MARKERS):
        return False
    if _labels_contain(labels, "CORTEX_STEP_TYPE_MCP", "mcp_tool"):
        return True
    if any(
        _labels_contain(labels, *markers)
        for markers, _tool_name in _CORTEX_OPERATION_TOOL_NAMES
    ):
        return True
    label_text = " ".join(labels).lower()
    return "mcp" in label_text and (
        "tool" in label_text or "cortex_step" in label_text
    )


def _tool_name_from_cortex_step(
    value: dict[str, Any], labels: list[str]
) -> str | None:
    for markers, tool_name in _CORTEX_OPERATION_TOOL_NAMES:
        if _labels_contain(labels, *markers):
            return tool_name
    if _labels_contain(labels, "CORTEX_STEP_TYPE_MCP", "mcp_tool", "mcp"):
        return _extract_tool_name(value) or "mcp_tool"
    return _extract_tool_name(value)


def _is_cortex_operation_completion(
    value: dict[str, Any], labels: list[str]
) -> bool:
    if _labels_contain(
        labels,
        "CORTEX_STEP_STATUS_DONE",
        "CORTEX_STEP_STATUS_COMPLETE",
        "complete",
        "completed",
        "finished",
        "success",
        "succeeded",
        "terminal",
        "done",
    ):
        return True
    if any(
        _find_first_value(value, key) not in (None, "")
        for key in _CORTEX_OPERATION_RESULT_KEYS
    ):
        return True
    return _find_first_value(value, "finishReason") is not None


def _is_done_event(value: dict[str, Any], labels: list[str]) -> bool:
    label_text = " ".join(labels)
    if any(
        marker in label_text
        for marker in ("complete", "completed", "finished", "terminal", "done")
    ):
        return True
    for key, item in value.items():
        compact = _compact_key(key)
        if compact in {"done", "complete", "completed", "finished", "terminal"}:
            return bool(item)
        if compact == "finishreason" and str(item or "").strip():
            return True
        if compact == "status" and str(item).lower() in {
            "done",
            "complete",
            "completed",
            "finished",
            "success",
            "succeeded",
            "terminal",
        }:
            return True
    return False


def _is_tool_event(value: dict[str, Any], labels: list[str]) -> bool:
    label_text = " ".join(labels)
    if any(marker in label_text for marker in ("tool", "function", "mcp")):
        return True
    return any(
        _compact_key(key)
        in {
            "toolcall",
            "toolcalls",
            "toolresult",
            "toolresults",
            "functioncall",
            "mcpserver",
        }
        for key in value
    )


def _is_waiting_event(labels: list[str]) -> bool:
    label_text = " ".join(labels)
    return any(
        marker in label_text
        for marker in ("waiting", "pending", "queued", "thinking", "in_progress")
    )


def _is_assistant_text_event(value: dict[str, Any], labels: list[str]) -> bool:
    role = str(value.get("role") or "").strip().lower()
    if role in {"user", "system", "tool", "function"}:
        return False
    if not any(_find_first_value(value, key) is not None for key in _TEXT_KEYS):
        return False
    label_text = " ".join(labels)
    if any(marker in label_text for marker in ("user", "human", "input")) and not any(
        marker in label_text for marker in ("assistant", "model", "agent", "response")
    ):
        return False
    if any(
        marker in label_text
        for marker in ("assistant", "model", "agent", "response", "text")
    ):
        return True
    return role == "" and (
        _find_first_value(value, "response") is not None
        or _find_first_value(value, "content") is not None
        or _find_first_value(value, "text") is not None
    )


def _extract_error_text(value: dict[str, Any]) -> str:
    for key in ("error", "exception", "failure", "message"):
        found = _find_first_value(value, key)
        if found is not None:
            return _coerce_to_text(found)
    return _extract_text(value)


def _extract_done_text(value: dict[str, Any]) -> str:
    for key in ("finishReason", "finish_reason", "status", "state", "message"):
        found = _find_first_value(value, key)
        if found is not None:
            return _coerce_to_text(found)
    return ""


def _extract_planner_response_text(value: dict[str, Any]) -> str:
    planner_response = _find_first_value(value, "plannerResponse")
    if not isinstance(planner_response, dict):
        planner_response = _find_first_value(value, "planner_response")
    if not isinstance(planner_response, dict):
        return ""
    for key in ("modifiedResponse", "modified_response", "response", "text", "content"):
        found = _find_first_value(planner_response, key)
        if found is not None:
            return _coerce_to_text(found)
    return ""


def _extract_text(value: dict[str, Any]) -> str:
    for key in _TEXT_KEYS:
        found = _find_first_value(value, key)
        if found is not None:
            return _coerce_to_text(found)
    return ""


def _extract_tool_name(value: dict[str, Any]) -> str | None:
    function = _find_first_value(value, "function")
    if isinstance(function, dict):
        nested_name = _find_first_value(function, "name")
        if nested_name is not None:
            return sanitize_console_text(nested_name)
    for key in _TOOL_NAME_KEYS:
        found = _find_first_value(value, key)
        if isinstance(found, str | int | float):
            return sanitize_console_text(found)
    return None


def _extract_tool_args(value: dict[str, Any]) -> dict[str, Any] | None:
    function = _find_first_value(value, "function")
    if isinstance(function, dict):
        nested_args = _find_first_value(function, "arguments")
        if nested_args is not None:
            return _coerce_tool_args(nested_args)
    for key in _TOOL_ARG_KEYS:
        found = _find_first_value(value, key)
        if found is not None:
            return _coerce_tool_args(found)
    return None


def _coerce_tool_args(value: Any) -> dict[str, Any] | None:
    value = _maybe_decode_json_string(value)
    sanitized = sanitize_cascade_payload(value)
    if isinstance(sanitized, dict):
        return sanitized
    if sanitized in (None, ""):
        return None
    return {"value": sanitized}


def _coerce_to_text(value: Any) -> str:
    value = _maybe_decode_json_string(value)
    if isinstance(value, str | int | float | bool):
        return truncate_cascade_text(value)
    if isinstance(value, list | tuple):
        pieces = []
        for item in value:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict):
                text = _extract_text(item)
                if text:
                    pieces.append(text)
        if pieces:
            return truncate_cascade_text("\n".join(pieces))
    if isinstance(value, dict):
        for key in _TEXT_KEYS:
            found = value.get(key)
            if found is not None and found is not value:
                return _coerce_to_text(found)
    try:
        return truncate_cascade_text(
            json.dumps(sanitize_cascade_payload(value), sort_keys=True)
        )
    except TypeError:
        return truncate_cascade_text(sanitize_cascade_payload(value))


def _find_first_value(value: Any, target_key: str) -> Any:
    target = _compact_key(target_key)
    if isinstance(value, dict):
        for key, item in value.items():
            if _compact_key(key) == target:
                return item
        for key, item in value.items():
            if _is_sensitive_key(key) or _is_header_key(key):
                continue
            if isinstance(item, dict | list | tuple):
                found = _find_first_value(item, target_key)
                if found is not None:
                    return found
    elif isinstance(value, list | tuple):
        for item in value:
            found = _find_first_value(item, target_key)
            if found is not None:
                return found
    return None


def _unknown_event(value: Any) -> AntigravityCascadeEvent:
    return AntigravityCascadeEvent(
        type="unknown",
        text=_summarize_payload(value),
        raw_kind=type(value).__name__,
    )


def _summarize_payload(value: Any) -> str:
    if value is None:
        return "empty payload"
    if isinstance(value, dict):
        keys = [sanitize_console_text(key, max_chars=40) for key in value.keys()]
        return f"dict keys={','.join(keys[:12])}"
    if isinstance(value, list | tuple):
        return f"list length={len(value)}"
    if isinstance(value, str):
        prefix = sanitize_console_text(value)
        return f"string length={len(value)} prefix={prefix}"
    return sanitize_console_text(value)


def _decode_response_payload(response: Any) -> Any:
    content = getattr(response, "content", None)
    if isinstance(content, bytes | bytearray) and content:
        messages = _decode_connect_json_envelopes(bytes(content))
        if messages:
            return messages
    try:
        return response.json()
    except ValueError:
        text = str(getattr(response, "text", "") or "")
    except AttributeError:
        text = ""

    stream_items: list[Any] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("data:"):
            stripped = stripped[5:].strip()
        if stripped == "[DONE]":
            stream_items.append({"done": True})
            continue
        stream_items.append(_maybe_decode_json_string(stripped))
    return stream_items if stream_items else text


def _encode_connect_json_envelope(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return bytes([0]) + len(body).to_bytes(4, "big") + body


def _decode_connect_json_envelopes(data: bytes) -> list[Any]:
    messages, _remaining = _pop_connect_json_messages(data)
    return messages


def _pop_connect_json_messages(buffer: bytes) -> tuple[list[Any], bytes]:
    messages: list[Any] = []
    position = 0
    while position + 5 <= len(buffer):
        length = int.from_bytes(buffer[position + 1 : position + 5], "big")
        start = position + 5
        end = start + length
        if end > len(buffer):
            break
        raw = buffer[start:end]
        position = end
        if not raw:
            continue
        try:
            messages.append(json.loads(raw.decode("utf-8", errors="replace")))
        except ValueError:
            messages.append({"decode_error": "invalid connect JSON envelope"})
    return messages, buffer[position:]


def _extract_cascade_id(payload: Any) -> str:
    for key in ("cascade_id", "cascadeId", "id"):
        found = _find_first_value(payload, key)
        if isinstance(found, str | int | float) and str(found).strip():
            return str(found).strip()
    return ""


class AntigravityCascadeClient:
    """Minimal, sanitized HTTP client for live Cascade exploration."""

    def __init__(
        self,
        *,
        endpoint: AntigravityEndpoint | None = None,
        client: httpx.Client | None = None,
        log_path: Path | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._endpoint = endpoint
        self._log_path = log_path
        self._owns_client = client is None
        self._cascade_models: dict[str, str] = {}
        self._http = client or httpx.Client(
            timeout=httpx.Timeout(
                connect=5.0,
                read=timeout,
                write=30.0,
                pool=10.0,
            ),
            verify=False,
        )

    def __enter__(self) -> AntigravityCascadeClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def start_cascade(
        self,
        *,
        model_enum: str,
        workspace_uri: str | None = None,
    ) -> AntigravityCascadeSession:
        """Start a Cascade session with a minimal probe payload."""

        enum = str(model_enum or "").strip()
        if not enum:
            raise AntigravityCascadeError("Antigravity Cascade model enum is required")
        payload: dict[str, Any] = {
            "requestedModel": enum,
            "source": "CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT",
            "trajectory_type": "CORTEX_TRAJECTORY_TYPE_CASCADE",
            "metadata": {
                "ide_name": "Antigravity",
                "extension_name": "Hermes",
                "extension_version": "probe",
                "locale": "en-US",
                "os": "Windows",
                "disable_telemetry": True,
            },
        }
        if workspace_uri:
            payload["workspace_uris"] = [workspace_uri]
            payload["override_workspace_uris"] = [workspace_uri]

        response = self._post("StartCascade", payload)
        decoded = _decode_response_payload(response)
        cascade_id = _extract_cascade_id(decoded)
        if not cascade_id:
            raise AntigravityCascadeError(
                "Antigravity StartCascade returned no cascade id"
            )
        self._cascade_models[cascade_id] = enum
        endpoint = self._ensure_endpoint()
        return AntigravityCascadeSession(
            cascade_id=cascade_id,
            base_url=endpoint.base_url,
            http_status=getattr(response, "status_code", None),
        )

    def send_user_message(self, cascade_id: str, message: str) -> int | None:
        """Send a user message to a Cascade session."""

        cid = str(cascade_id or "").strip()
        if not cid:
            raise AntigravityCascadeError("Antigravity Cascade id is required")
        payload = {
            "cascade_id": cid,
            "items": [{"text": str(message or "")}],
        }
        model_enum = self._cascade_models.get(cid)
        if model_enum:
            payload["cascade_config"] = {
                "planner_config": {"plan_model": model_enum},
            }
        response = self._post("SendUserCascadeMessage", payload)
        return getattr(response, "status_code", None)

    def stream_agent_state_updates(
        self,
        cascade_id: str,
        *,
        max_events: int = 100,
    ) -> Iterator[AntigravityCascadeEvent]:
        """Yield sanitized parsed events from Cascade state updates."""

        cid = str(cascade_id or "").strip()
        if not cid:
            raise AntigravityCascadeError("Antigravity Cascade id is required")
        if max_events <= 0:
            return
        payload = {
            "conversation_id": cid,
            "subscriber_id": f"hermes-probe-{uuid.uuid4().hex}",
            "trajectory_verbosity": "CLIENT_TRAJECTORY_VERBOSITY_PROD_UI",
            "initial_steps_page_bounds": {
                "start_index": 0,
                "end_index_exclusive": max_events,
            },
            "initial_generator_metadatas_page_bounds": {
                "start_index": 0,
                "end_index_exclusive": max_events,
            },
            "initial_executor_metadatas_page_bounds": {
                "start_index": 0,
                "end_index_exclusive": max_events,
            },
        }
        if hasattr(self._http, "stream"):
            yielded = 0
            buffer = b""
            try:
                with self._http.stream(
                    "POST",
                    self._url("StreamAgentStateUpdates"),
                    content=_encode_connect_json_envelope(payload),
                    headers=self._headers(connect_json=True),
                ) as response:
                    status_code = getattr(response, "status_code", None)
                    if status_code != 200:
                        raise AntigravityCascadeError(
                            f"Antigravity StreamAgentStateUpdates HTTP {status_code}: response body omitted",
                            status_code=status_code,
                        )
                    for chunk in response.iter_bytes():
                        buffer += chunk
                        messages, buffer = _pop_connect_json_messages(buffer)
                        for message in messages:
                            for event in parse_cascade_events(message):
                                yield event
                                yielded += 1
                                if yielded >= max_events:
                                    return
            except httpx.ReadTimeout:
                if yielded:
                    # Mid-generation stall after partial output: do NOT end
                    # silently — a bare return is indistinguishable from a clean
                    # stream close and would be reported as finish_reason="stop"
                    # (a complete answer). Surface a distinguishable truncation
                    # so the adapter can mark the reply incomplete instead.
                    truncated = AntigravityCascadeError(
                        "Antigravity StreamAgentStateUpdates timed out mid-generation"
                    )
                    truncated.code = "antigravity_cascade_truncated"
                    raise truncated from None
                raise AntigravityCascadeError(
                    "Antigravity StreamAgentStateUpdates timed out before events"
                ) from None
            return

        response = self._post("StreamAgentStateUpdates", payload)
        payload = _decode_response_payload(response)
        for event in parse_cascade_events(payload)[:max_events]:
            yield event

    def _ensure_endpoint(self) -> AntigravityEndpoint:
        if self._endpoint is not None:
            return self._endpoint
        try:
            self._endpoint = discover_antigravity_endpoint(
                log_path=self._log_path,
                client=self._http,
            )
        except AntigravityBridgeError as exc:
            raise AntigravityCascadeError(str(exc)) from exc
        return self._endpoint

    def _post(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = self._http.post(
                self._url(method),
                json=payload,
                headers=self._headers(),
            )
        except Exception as exc:
            message = sanitize_console_text(exc)
            raise AntigravityCascadeError(
                f"Antigravity {method} request failed: {message}"
            ) from exc

        status_code = getattr(response, "status_code", None)
        if status_code != 200:
            raise AntigravityCascadeError(
                f"Antigravity {method} HTTP {status_code}: response body omitted",
                status_code=status_code,
            )
        return response

    def _url(self, method: str) -> str:
        endpoint = self._ensure_endpoint()
        return f"{endpoint.base_url}{_SERVICE_PREFIX}{method}"

    def _headers(self, *, connect_json: bool = False) -> dict[str, str]:
        endpoint = self._ensure_endpoint()
        content_type = (
            _CONNECT_JSON_CONTENT_TYPE if connect_json else "application/json"
        )
        accept = _CONNECT_JSON_CONTENT_TYPE if connect_json else "application/json"
        return {
            "Content-Type": content_type,
            "Accept": accept,
            "x-codeium-csrf-token": endpoint.csrf_token,
        }
