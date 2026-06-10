from __future__ import annotations

from types import SimpleNamespace

from agent.google_antigravity_cascade import (
    AntigravityCascadeError,
    AntigravityCascadeEvent,
    AntigravityCascadeSession,
)
from scripts import probe_antigravity_cascade as probe


def test_stream_summary_reports_counts_and_types():
    events = [
        AntigravityCascadeEvent(type="waiting", text="running"),
        AntigravityCascadeEvent(type="assistant_text", text="OK"),
        AntigravityCascadeEvent(type="done", text="stop"),
    ]

    summary = probe.summarize_events(events)

    assert probe.format_stream_line(summary) == (
        "STREAM events=3 assistant_text=True done=True error=False "
        "tool_call=False tool_result=False unknown=False "
        "types=waiting,assistant_text,done"
    )


def test_stream_summary_reports_tool_and_unknown_visibility():
    events = [
        AntigravityCascadeEvent(type="tool_call", tool_name="run_command"),
        AntigravityCascadeEvent(type="tool_result", tool_name="read_file"),
        AntigravityCascadeEvent(type="unknown", text="dict keys=opaque"),
    ]

    summary = probe.summarize_events(events)

    assert summary["tool_call"] is True
    assert summary["tool_result"] is True
    assert summary["unknown"] is True
    assert probe.format_stream_line(summary) == (
        "STREAM events=3 assistant_text=False done=False error=False "
        "tool_call=True tool_result=True unknown=True "
        "types=tool_call,tool_result,unknown"
    )


def test_assistant_text_prefix_is_short_and_sanitized():
    events = [
        AntigravityCascadeEvent(
            type="assistant_text",
            text="OK api_key=secret-value " + ("x" * 120),
        )
    ]

    line = probe.summarize_events(events)["text_prefix"]

    assert "secret-value" not in line
    assert "[REDACTED]" in line
    assert "...[truncated]" in line


def test_format_probe_error_does_not_leak_sensitive_strings():
    error = AntigravityCascadeError(
        "failed Authorization: Bearer bearer-secret csrfToken=csrf-secret "
        "Cookie=session-secret",
        status_code=500,
    )

    line = probe.format_error_line(error)

    assert "antigravity_cascade_error" in line
    assert "status=500" in line
    assert "bearer-secret" not in line
    assert "csrf-secret" not in line
    assert "session-secret" not in line
    assert "[REDACTED]" in line


def test_parser_defaults_workspace_uri_to_none():
    args = probe.build_parser().parse_args([])

    assert args.workspace_uri is None


def test_main_passes_workspace_uri_to_client(monkeypatch, capsys):
    class FakeProbeClient:
        start_kwargs: dict | None = None

        def __init__(self, *, timeout: float):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_exc_info):
            return None

        def start_cascade(self, **kwargs):
            type(self).start_kwargs = kwargs
            return AntigravityCascadeSession(
                cascade_id="cascade-test",
                base_url="https://127.0.0.1:6000",
                http_status=200,
            )

        def send_user_message(self, cascade_id: str, message: str):
            return 200

        def stream_agent_state_updates(self, cascade_id: str, *, max_events: int):
            return [
                AntigravityCascadeEvent(type="tool_result", tool_name="list_dir"),
                AntigravityCascadeEvent(type="assistant_text", text="OK"),
            ]

    monkeypatch.setattr(
        probe,
        "check_antigravity_available",
        lambda: SimpleNamespace(
            available=True,
            base_url="https://127.0.0.1:6000",
            reason="",
        ),
    )
    monkeypatch.setattr(probe, "AntigravityCascadeClient", FakeProbeClient)

    result = probe.main(
        [
            "--prompt",
            "Inspect fixtures only.",
            "--workspace-uri",
            "file:///C:/Users/RDP/Projects/hermes-agent-cascade-probe",
            "--max-events",
            "5",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert FakeProbeClient.start_kwargs is not None
    assert FakeProbeClient.start_kwargs["workspace_uri"] == (
        "file:///C:/Users/RDP/Projects/hermes-agent-cascade-probe"
    )
    assert "tool_result=True" in captured.out
