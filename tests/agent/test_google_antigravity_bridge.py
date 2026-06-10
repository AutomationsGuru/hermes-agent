from __future__ import annotations

from pathlib import Path

import pytest


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, *, get_results=None, post_response=None):
        self.get_results = get_results or {}
        self.post_response = post_response or FakeResponse(payload={"response": "OK"})
        self.get_urls: list[str] = []
        self.post_calls: list[tuple[str, dict, dict]] = []
        self.closed = False

    def get(self, url: str):
        self.get_urls.append(url)
        result = self.get_results.get(url)
        if isinstance(result, BaseException):
            raise result
        if result is None:
            return FakeResponse(status_code=404, text="not found")
        return result

    def post(self, url: str, *, json: dict, headers: dict):
        self.post_calls.append((url, json, headers))
        return self.post_response

    def close(self):
        self.closed = True


def _html(csrf: str = "csrf-secret") -> str:
    return f'<script>window.__APP_CONFIG__={{"csrfToken":"{csrf}"}}</script>'


def test_discover_endpoint_prefers_env_override_and_extracts_csrf(
    monkeypatch,
    tmp_path: Path,
):
    from agent import google_antigravity_bridge as bridge

    monkeypatch.setenv("HERMES_ANTIGRAVITY_URL", "https://127.0.0.1:6000/")
    log_path = tmp_path / "main.log"
    log_path.write_text("Local: https://127.0.0.1:5000/\n", encoding="utf-8")
    fake = FakeClient(
        get_results={
            "https://127.0.0.1:6000": FakeResponse(text=_html("env-csrf")),
        }
    )

    endpoint = bridge.discover_antigravity_endpoint(log_path=log_path, client=fake)

    assert endpoint.base_url == "https://127.0.0.1:6000"
    assert endpoint.csrf_token == "env-csrf"
    assert fake.get_urls == ["https://127.0.0.1:6000"]


def test_discover_endpoint_uses_newest_log_url_when_env_missing(
    monkeypatch,
    tmp_path: Path,
):
    from agent import google_antigravity_bridge as bridge

    monkeypatch.delenv("HERMES_ANTIGRAVITY_URL", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_URL", raising=False)
    log_path = tmp_path / "main.log"
    log_path.write_text(
        "Local: https://127.0.0.1:5000/\nLocal: https://127.0.0.1:5001/\n",
        encoding="utf-8",
    )
    fake = FakeClient(
        get_results={
            "https://127.0.0.1:5001": FakeResponse(text=_html("newest-csrf")),
        }
    )

    endpoint = bridge.discover_antigravity_endpoint(log_path=log_path, client=fake)

    assert endpoint.base_url == "https://127.0.0.1:5001"
    assert endpoint.csrf_token == "newest-csrf"
    assert fake.get_urls == ["https://127.0.0.1:5001"]


def test_check_antigravity_available_reports_available_and_redacts_csrf(
    monkeypatch,
    tmp_path: Path,
):
    from agent import google_antigravity_bridge as bridge

    monkeypatch.delenv("HERMES_ANTIGRAVITY_URL", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_URL", raising=False)
    log_path = tmp_path / "main.log"
    log_path.write_text("Local: https://127.0.0.1:5001/\n", encoding="utf-8")
    fake = FakeClient(
        get_results={
            "https://127.0.0.1:5001": FakeResponse(text=_html("csrf-secret")),
        }
    )

    status = bridge.check_antigravity_available(log_path=log_path, client=fake)

    assert status.available is True
    assert status.base_url == "https://127.0.0.1:5001"
    assert status.reason == ""
    assert "csrf-secret" not in repr(status)


def test_check_antigravity_available_reports_clean_actionable_unavailable(
    monkeypatch,
    tmp_path: Path,
):
    from agent import google_antigravity_bridge as bridge

    monkeypatch.delenv("HERMES_ANTIGRAVITY_URL", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_URL", raising=False)
    log_path = tmp_path / "main.log"
    log_path.write_text("Local: https://127.0.0.1:5001/\n", encoding="utf-8")
    fake = FakeClient(
        get_results={
            "https://127.0.0.1:5001": OSError("[WinError 10061] refused"),
        }
    )

    status = bridge.check_antigravity_available(log_path=log_path, client=fake)

    assert status.available is False
    assert status.base_url == ""
    assert "Start Antigravity" in status.reason
    assert "HERMES_ANTIGRAVITY_URL" in status.reason
    assert "WinError" not in status.reason
    assert "10061" not in status.reason


def test_get_model_response_discovery_failure_uses_clean_actionable_error(
    monkeypatch,
    tmp_path: Path,
):
    from agent import google_antigravity_bridge as bridge

    monkeypatch.delenv("HERMES_ANTIGRAVITY_URL", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_URL", raising=False)
    log_path = tmp_path / "main.log"
    log_path.write_text("Local: https://127.0.0.1:5001/\n", encoding="utf-8")
    fake = FakeClient(
        get_results={
            "https://127.0.0.1:5001": OSError("[WinError 10061] refused"),
        }
    )

    with pytest.raises(bridge.AntigravityBridgeError) as excinfo:
        bridge.get_model_response(
            model_enum="MODEL_PLACEHOLDER_M132",
            prompt="Return OK",
            client=fake,
            log_path=log_path,
        )

    message = str(excinfo.value)
    assert "Start Antigravity" in message
    assert "HERMES_ANTIGRAVITY_URL" in message
    assert "WinError" not in message
    assert "10061" not in message
