"""Tests for the ``hermes webhook`` CLI helpers."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json

import pytest

import hermes_cli.webhook as webhook_cli


def _args(action: str, **overrides):
    defaults = {
        "webhook_action": action,
        "name": "sample",
        "prompt": "",
        "events": "",
        "description": "",
        "skills": "",
        "deliver": "log",
        "deliver_chat_id": "",
        "secret": "",
        "deliver_only": False,
        "payload": "",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def enabled_webhook_env(monkeypatch):
    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("WEBHOOK_HOST", "127.0.0.1")
    monkeypatch.setenv("WEBHOOK_PORT", "9878")
    monkeypatch.setenv("WEBHOOK_SECRET", "env-secret")


def test_enabled_detection_defaults_to_false():
    assert webhook_cli._is_webhook_enabled() is False


def test_enabled_detection_accepts_env_only(enabled_webhook_env):
    assert webhook_cli._is_webhook_enabled() is True
    assert webhook_cli._get_webhook_base_url() == "http://127.0.0.1:9878"


def test_subscribe_list_remove_flow(enabled_webhook_env, capsys):
    webhook_cli.webhook_command(
        _args(
            "subscribe",
            name="Sample Hook",
            prompt="Issue {issue.number}: {issue.title}",
            events="issues,pull_request",
            description="Issue router",
            secret="route-secret",
        )
    )
    out = capsys.readouterr().out
    assert "Created webhook subscription: sample-hook" in out
    assert "http://127.0.0.1:9878/webhooks/sample-hook" in out

    subs = webhook_cli._load_subscriptions()
    assert subs["sample-hook"]["secret"] == "route-secret"
    assert subs["sample-hook"]["events"] == ["issues", "pull_request"]

    webhook_cli.webhook_command(_args("list"))
    out = capsys.readouterr().out
    assert "sample-hook" in out
    assert "Issue router" in out

    webhook_cli.webhook_command(_args("remove", name="sample-hook"))
    out = capsys.readouterr().out
    assert "Removed webhook subscription: sample-hook" in out
    assert webhook_cli._load_subscriptions() == {}


def test_test_command_sends_signed_request(enabled_webhook_env, monkeypatch, capsys):
    import urllib.request

    webhook_cli._save_subscriptions(
        {
            "sample": {
                "secret": "route-secret",
                "prompt": "Test {message}",
                "deliver": "log",
            }
        }
    )

    captured = {}

    class _FakeResponse:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"accepted"}'

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["signature"] = (
            request.get_header("X-hub-signature-256")
            or request.get_header("X-Hub-Signature-256")
        )
        captured["event"] = (
            request.get_header("X-github-event")
            or request.get_header("X-GitHub-Event")
        )
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    payload = '{"message":"hello"}'

    webhook_cli.webhook_command(_args("test", name="sample", payload=payload))

    out = capsys.readouterr().out
    assert "Sending test POST to http://127.0.0.1:9878/webhooks/sample" in out
    assert 'Response (202): {"status":"accepted"}' in out
    assert captured["url"] == "http://127.0.0.1:9878/webhooks/sample"
    assert captured["body"] == payload.encode()
    expected = "sha256=" + hmac.new(
        b"route-secret", payload.encode(), hashlib.sha256
    ).hexdigest()
    assert captured["signature"] == expected
    assert captured["event"] == "test"
    assert captured["timeout"] == 10


def test_subscribe_rejects_deliver_only_log(enabled_webhook_env, capsys):
    webhook_cli.webhook_command(
        _args(
            "subscribe",
            name="notify",
            prompt="Alert {message}",
            deliver_only=True,
        )
    )

    out = capsys.readouterr().out
    assert "--deliver-only requires --deliver to be a real target" in out
    assert webhook_cli._load_subscriptions() == {}
