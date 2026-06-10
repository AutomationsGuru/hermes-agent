from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import httpx
import pytest

from agent.google_antigravity_bridge import AntigravityEndpoint
from agent.google_antigravity_cascade import (
    AntigravityCascadeClient,
    AntigravityCascadeError,
    AntigravityCascadeEvent,
    _encode_connect_json_envelope,
    parse_cascade_events,
    sanitize_cascade_payload,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "antigravity"


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.post_calls: list[tuple[str, dict, dict]] = []
        self.closed = False

    def post(self, url: str, *, json: dict, headers: dict):
        self.post_calls.append((url, json, headers))
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse(payload={})

    def close(self):
        self.closed = True


def _fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_sanitizer_redacts_sensitive_keys_recursively_case_insensitive():
    payload = {
        "Authorization": "Bearer authorization-secret",
        "nested": {
            "csrfToken": "csrf-secret",
            "normal": "visible",
            "headers": {"X-Trace": "raw-header"},
        },
        "items": [{"api_key": "api-secret"}, {"Cookie": "cookie-secret"}],
    }

    sanitized = sanitize_cascade_payload(payload)

    rendered = repr(sanitized)
    assert "authorization-secret" not in rendered
    assert "csrf-secret" not in rendered
    assert "api-secret" not in rendered
    assert "cookie-secret" not in rendered
    assert "raw-header" not in rendered
    assert sanitized["nested"]["normal"] == "visible"
    assert sanitized["nested"]["headers"] == "[REDACTED_HEADERS]"


def test_sanitizer_truncates_long_strings():
    sanitized = sanitize_cascade_payload({"text": "x" * 40}, max_string_length=10)

    assert sanitized["text"] == "xxxxxxxxxx...[truncated]"


def test_normalized_event_has_no_raw_payload_field():
    event = parse_cascade_events({"role": "assistant", "content": "OK"})[0]

    assert isinstance(event, AntigravityCascadeEvent)
    assert "raw" not in {field.name for field in fields(event)}
    assert "payload" not in {field.name for field in fields(event)}


def test_parse_assistant_text_fixture():
    events = parse_cascade_events(_fixture("cascade_assistant_text.json"))

    assert [event.type for event in events] == ["assistant_text"]
    assert events[0].text == "OK"


def test_parse_done_fixture():
    events = parse_cascade_events(_fixture("cascade_done.json"))

    assert any(event.type == "done" for event in events)
    assert events[-1].raw_kind in {"cascade_status", "completed"}


def test_parse_conversation_idle_fixture_emits_conversation_done():
    events = parse_cascade_events(_fixture("cascade_conversation_idle.json"))

    done = [event for event in events if event.conversation_done]
    assert len(done) == 1
    assert done[0].type == "done"
    assert done[0].raw_kind == "CASCADE_RUN_STATUS_IDLE"


def test_parse_running_status_does_not_emit_conversation_done():
    payload = {
        "update": {
            "conversationId": "conversation-test",
            "trajectoryId": "trajectory-test",
            "status": "CASCADE_RUN_STATUS_RUNNING",
            "executableStatus": "CASCADE_RUN_STATUS_IDLE",
            "executorLoopStatus": "CASCADE_RUN_STATUS_RUNNING",
        }
    }

    events = parse_cascade_events(payload)

    assert not any(event.conversation_done for event in events)


def test_step_level_done_events_are_not_conversation_done():
    for fixture_name in ("cascade_done.json", "cascade_list_directory_step.json"):
        events = parse_cascade_events(_fixture(fixture_name))
        assert events
        assert not any(event.conversation_done for event in events)


def test_idle_update_with_planner_text_orders_text_before_conversation_done():
    payload = {
        "update": {
            "conversationId": "conversation-test",
            "status": "CASCADE_RUN_STATUS_IDLE",
            "mainTrajectoryUpdate": {
                "stepsUpdate": {
                    "steps": [
                        {
                            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                            "status": "CORTEX_STEP_STATUS_DONE",
                            "plannerResponse": {
                                "modifiedResponse": "FINAL",
                                "messageId": "bot-test",
                            },
                        }
                    ]
                }
            },
        }
    }

    events = parse_cascade_events(payload)

    text_index = next(
        index
        for index, event in enumerate(events)
        if event.type == "assistant_text" and event.text == "FINAL"
    )
    done_index = next(
        index for index, event in enumerate(events) if event.conversation_done
    )
    assert text_index < done_index


def test_parse_planner_response_step_as_assistant_text():
    payload = {
        "update": {
            "mainTrajectoryUpdate": {
                "stepsUpdate": {
                    "steps": [
                        {
                            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                            "status": "CORTEX_STEP_STATUS_DONE",
                            "plannerResponse": {
                                "modifiedResponse": "OK",
                                "messageId": "bot-test",
                            },
                        }
                    ]
                }
            }
        }
    }

    events = parse_cascade_events(payload)

    assert any(
        event.type == "assistant_text" and event.text == "OK" for event in events
    )


def test_parse_list_directory_step_as_tool_result_with_tool_name():
    events = parse_cascade_events(_fixture("cascade_list_directory_step.json"))

    operation = next(
        event for event in events if event.raw_kind == "CORTEX_STEP_TYPE_LIST_DIRECTORY"
    )
    assert operation.type == "tool_result"
    assert operation.tool_name == "list_dir"
    assert operation.tool_args == {"path": "tests/fixtures/antigravity"}
    assert "cascade_done.json" in operation.text
    assert not hasattr(operation, "handler")
    assert not hasattr(operation, "callable")
    assert "raw" not in {field.name for field in fields(operation)}
    assert "payload" not in {field.name for field in fields(operation)}


def test_parse_read_file_step_redacts_sensitive_tool_args_and_result_text():
    events = parse_cascade_events(_fixture("cascade_read_file_step.json"))

    operation = next(
        event for event in events if event.raw_kind == "CORTEX_STEP_TYPE_READ_FILE"
    )
    assert operation.type == "tool_result"
    assert operation.tool_name == "read_file"
    assert operation.tool_args == {
        "path": "tests/fixtures/antigravity/cascade_assistant_text.json",
        "Authorization": "[REDACTED]",
    }
    assert "read-secret" not in operation.text
    assert "file-secret" not in repr(operation)
    assert "[REDACTED]" in operation.text


def test_parse_run_command_step_observes_tool_call_without_execution_hook():
    events = parse_cascade_events(_fixture("cascade_run_command_step.json"))

    operation = next(
        event for event in events if event.raw_kind == "CORTEX_STEP_TYPE_RUN_COMMAND"
    )
    assert operation.type == "tool_call"
    assert operation.tool_name == "run_command"
    assert operation.tool_args == {"command": "python -m pytest --version"}
    assert not hasattr(operation, "handler")
    assert not hasattr(operation, "callable")


def test_parse_error_fixture_redacts_sensitive_values():
    events = parse_cascade_events(_fixture("cascade_error.json"))

    assert events[0].type == "error"
    rendered = repr(events[0])
    assert "csrf-secret" not in rendered
    assert "bearer-secret" not in rendered


def test_parse_tool_fixture_observes_tool_without_executable_action():
    events = parse_cascade_events(_fixture("cascade_tool_call.json"))

    assert events[0].type == "tool_call"
    assert events[0].tool_name == "ListMcpResources"
    assert events[0].tool_args == {"server": "probe", "api_key": "[REDACTED]"}
    assert not hasattr(events[0], "handler")
    assert not hasattr(events[0], "callable")


def test_parse_unknown_fixture_returns_safe_unknown_event():
    events = parse_cascade_events(_fixture("cascade_unknown.json"))

    assert len(events) == 1
    assert events[0].type == "unknown"
    assert "unknown-secret" not in repr(events[0])


def test_parse_unknown_future_cortex_step_is_safe():
    events = parse_cascade_events(
        {
            "type": "CORTEX_STEP_TYPE_FUTURE_WIDGET",
            "status": "CORTEX_STEP_STATUS_DONE",
            "result": {
                "message": "future-secret csrfToken=future-secret",
            },
        }
    )

    assert events
    assert all(event.type in {"done", "unknown"} for event in events)
    assert "future-secret" not in repr(events)


def test_client_methods_call_expected_suffixes_and_keep_csrf_internal():
    endpoint = AntigravityEndpoint(
        base_url="https://127.0.0.1:6000",
        csrf_token="csrf-secret",
    )
    fake = FakeClient([
        FakeResponse(payload={"cascade_id": "server-cascade"}),
        FakeResponse(payload={"status": "accepted"}),
        FakeResponse(payload=_fixture("cascade_assistant_text.json")),
    ])
    client = AntigravityCascadeClient(endpoint=endpoint, client=fake)

    session = client.start_cascade(model_enum="MODEL_PLACEHOLDER_M132")
    send_status = client.send_user_message(session.cascade_id, "Return exactly OK.")
    events = list(client.stream_agent_state_updates(session.cascade_id))

    assert [call[0].rsplit("/", 1)[-1] for call in fake.post_calls] == [
        "StartCascade",
        "SendUserCascadeMessage",
        "StreamAgentStateUpdates",
    ]
    send_payload = fake.post_calls[1][1]
    assert send_payload["cascade_config"] == {
        "planner_config": {"plan_model": "MODEL_PLACEHOLDER_M132"}
    }
    stream_payload = fake.post_calls[2][1]
    assert stream_payload["conversation_id"] == "server-cascade"
    assert all(
        call[2]["x-codeium-csrf-token"] == "csrf-secret" for call in fake.post_calls
    )
    assert session.cascade_id == "server-cascade"
    assert session.http_status == 200
    assert send_status == 200
    assert events[0].type == "assistant_text"
    rendered_results = repr((session, send_status, events))
    assert "csrf-secret" not in rendered_results


def test_client_non_200_errors_are_sanitized():
    endpoint = AntigravityEndpoint(
        base_url="https://127.0.0.1:6000",
        csrf_token="csrf-secret",
    )
    fake = FakeClient([
        FakeResponse(
            status_code=500,
            payload={
                "message": "bad request",
                "csrfToken": "csrf-secret",
                "Authorization": "Bearer bearer-secret",
            },
        )
    ])
    client = AntigravityCascadeClient(endpoint=endpoint, client=fake)

    with pytest.raises(AntigravityCascadeError) as excinfo:
        client.start_cascade(model_enum="MODEL_PLACEHOLDER_M132")

    error = excinfo.value
    assert error.code == "antigravity_cascade_error"
    assert error.status_code == 500
    assert "StartCascade HTTP 500" in str(error)
    assert "csrf-secret" not in str(error)
    assert "bearer-secret" not in str(error)


# ---------------------------------------------------------------------------
# Production Connect streaming path coverage.
#
# A real httpx.Client exposes ``.stream``, so production ALWAYS takes the
# Connect-envelope branch of ``stream_agent_state_updates``. The ``FakeClient``
# above only has ``.post`` and therefore exercises the JSON fallback. These
# doubles provide a ``.stream`` context manager so the envelope framing,
# connect+json headers, status guard, and mid-stream ReadTimeout handling are
# actually executed by tests.
# ---------------------------------------------------------------------------


class FakeStreamResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        chunks=(),
        raise_before: bool = False,
        raise_after_chunk: int | None = None,
    ):
        self.status_code = status_code
        self._chunks = list(chunks)
        self._raise_before = raise_before
        self._raise_after_chunk = raise_after_chunk

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        return False

    def iter_bytes(self):
        if self._raise_before:
            raise httpx.ReadTimeout("simulated stall before any event")
        emitted = 0
        for chunk in self._chunks:
            yield chunk
            emitted += 1
            if (
                self._raise_after_chunk is not None
                and emitted >= self._raise_after_chunk
            ):
                raise httpx.ReadTimeout("simulated mid-generation stall")


class FakeStreamClient:
    def __init__(self, response: FakeStreamResponse):
        self._response = response
        self.stream_calls: list[tuple] = []

    def stream(self, method, url, *, content, headers):
        self.stream_calls.append((method, url, content, headers))
        return self._response

    def close(self):
        pass


def _stream_client(response: FakeStreamResponse) -> AntigravityCascadeClient:
    endpoint = AntigravityEndpoint(
        base_url="https://127.0.0.1:6000",
        csrf_token="csrf-secret",
    )
    return AntigravityCascadeClient(endpoint=endpoint, client=FakeStreamClient(response))


def test_stream_path_reassembles_frames_split_across_chunk_boundaries():
    msg = _fixture("cascade_assistant_text.json")
    expected_types = [e.type for e in parse_cascade_events(msg)] * 2

    full = _encode_connect_json_envelope(msg) + _encode_connect_json_envelope(msg)
    # Split mid-way through the second frame's 5-byte header to prove the
    # buffer correctly reassembles a frame spanning two iter_bytes() chunks.
    split = len(_encode_connect_json_envelope(msg)) + 3
    response = FakeStreamResponse(chunks=[full[:split], full[split:]])
    client = _stream_client(response)

    events = list(client.stream_agent_state_updates("server-cascade"))

    assert [e.type for e in events] == expected_types
    # Production took the Connect branch with connect+json framing/headers.
    fake = client._http
    _method, url, content, headers = fake.stream_calls[0]
    assert url.rsplit("/", 1)[-1] == "StreamAgentStateUpdates"
    assert headers["Content-Type"] == "application/connect+json"
    assert headers["x-codeium-csrf-token"] == "csrf-secret"
    assert content[:1] == b"\x00"  # envelope flag byte
    assert "csrf-secret" not in repr(events)


def test_stream_path_non_200_raises_sanitized_without_body():
    response = FakeStreamResponse(status_code=500, chunks=[b"super-secret-body"])
    client = _stream_client(response)

    with pytest.raises(AntigravityCascadeError) as excinfo:
        list(client.stream_agent_state_updates("server-cascade"))

    error = excinfo.value
    assert error.status_code == 500
    assert "response body omitted" in str(error)
    assert "super-secret-body" not in str(error)


def test_stream_path_readtimeout_after_events_is_truncated_not_silent():
    msg = _fixture("cascade_assistant_text.json")
    response = FakeStreamResponse(
        chunks=[_encode_connect_json_envelope(msg)],
        raise_after_chunk=1,
    )
    client = _stream_client(response)

    events = []
    with pytest.raises(AntigravityCascadeError) as excinfo:
        for event in client.stream_agent_state_updates("server-cascade"):
            events.append(event)

    # Partial events were delivered before the stall, then a DISTINGUISHABLE
    # truncation surfaced (so the adapter can mark the reply incomplete rather
    # than reporting a clean finish_reason="stop").
    assert events, "expected at least one event before the stall"
    assert excinfo.value.code == "antigravity_cascade_truncated"


def test_stream_path_readtimeout_before_any_event_raises():
    response = FakeStreamResponse(raise_before=True)
    client = _stream_client(response)

    with pytest.raises(AntigravityCascadeError) as excinfo:
        list(client.stream_agent_state_updates("server-cascade"))

    # No partial output: a generic timeout error (not the truncation code).
    assert excinfo.value.code == "antigravity_cascade_error"
    assert "before events" in str(excinfo.value)
