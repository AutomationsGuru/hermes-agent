"""Tests for the generic webhook gateway adapter."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

try:
    from aiohttp import ClientSession
except ImportError:  # pragma: no cover - covered by AIOHTTP_AVAILABLE skip
    ClientSession = None

from gateway.config import (
    GatewayConfig,
    Platform,
    PlatformConfig,
    _apply_env_overrides,
)
from gateway.platforms.base import SendResult
from gateway.platforms.webhook import AIOHTTP_AVAILABLE, WebhookAdapter


def _make_adapter(**extra_overrides) -> WebhookAdapter:
    extra = {
        "host": "127.0.0.1",
        "secret": "global-secret",
        "routes": {},
    }
    extra.update(extra_overrides)
    return WebhookAdapter(PlatformConfig(enabled=True, extra=extra))


def _json_body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _github_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


class _FakeRequest:
    def __init__(
        self,
        route_name: str,
        body: bytes,
        *,
        headers: dict | None = None,
    ):
        self.match_info = {"route_name": route_name}
        self.headers = headers or {}
        self.content_length = len(body)
        self.method = "POST"
        self._body = body

    async def read(self) -> bytes:
        return self._body


class TestWebhookConfig:
    def test_gateway_config_accepts_enabled_static_webhook_platform(self):
        config = GatewayConfig.from_dict(
            {
                "platforms": {
                    "webhook": {
                        "enabled": True,
                        "extra": {
                            "host": "127.0.0.1",
                            "port": 9876,
                            "secret": "cfg-secret",
                            "routes": {"static": {"prompt": "ok"}},
                        },
                    }
                }
            }
        )

        platform_config = config.platforms[Platform.WEBHOOK]
        assert platform_config.enabled is True
        assert platform_config.extra["host"] == "127.0.0.1"
        assert platform_config.extra["port"] == 9876
        assert Platform.WEBHOOK in config.get_connected_platforms()

    def test_gateway_config_excludes_disabled_webhook_platform(self):
        config = GatewayConfig.from_dict(
            {"platforms": {"webhook": {"enabled": False, "extra": {}}}}
        )

        assert Platform.WEBHOOK not in config.get_connected_platforms()

    def test_env_overrides_create_and_configure_webhook_platform(self, monkeypatch):
        config = GatewayConfig()

        monkeypatch.setenv("WEBHOOK_ENABLED", "true")
        monkeypatch.setenv("WEBHOOK_HOST", "127.0.0.1")
        monkeypatch.setenv("WEBHOOK_PORT", "9877")
        monkeypatch.setenv("WEBHOOK_SECRET", "env-secret")

        _apply_env_overrides(config)

        platform_config = config.platforms[Platform.WEBHOOK]
        assert platform_config.enabled is True
        assert platform_config.extra["host"] == "127.0.0.1"
        assert platform_config.extra["port"] == 9877
        assert platform_config.extra["secret"] == "env-secret"

    def test_env_overrides_can_disable_config_enabled_webhook(self, monkeypatch):
        config = GatewayConfig(
            platforms={
                Platform.WEBHOOK: PlatformConfig(
                    enabled=True,
                    extra={"host": "127.0.0.1", "port": 9877},
                )
            }
        )

        monkeypatch.setenv("WEBHOOK_ENABLED", "false")

        _apply_env_overrides(config)

        assert config.platforms[Platform.WEBHOOK].enabled is False


class TestWebhookRoutes:
    def test_static_routes_load_from_platform_config(self):
        adapter = _make_adapter(
            routes={
                "static": {
                    "events": ["push"],
                    "secret": "route-secret",
                    "prompt": "Push to {repository.full_name}",
                    "deliver": "log",
                }
            }
        )

        assert adapter._routes["static"]["secret"] == "route-secret"
        assert adapter._routes["static"]["events"] == ["push"]

    def test_dynamic_routes_load_from_subscriptions_file(self):
        from hermes_cli.webhook import _save_subscriptions

        _save_subscriptions(
            {
                "dynamic": {
                    "secret": "dynamic-secret",
                    "prompt": "Dynamic {message}",
                    "deliver": "log",
                }
            }
        )
        adapter = _make_adapter()

        adapter._reload_dynamic_routes()

        assert adapter._routes["dynamic"]["secret"] == "dynamic-secret"
        assert adapter._routes["dynamic"]["prompt"] == "Dynamic {message}"


class TestWebhookHttp:
    @pytest.mark.asyncio
    async def test_health_endpoint_starts_on_configured_port(self, unused_tcp_port):
        if not AIOHTTP_AVAILABLE or ClientSession is None:
            pytest.skip("aiohttp not installed")

        adapter = _make_adapter(port=unused_tcp_port)
        try:
            assert await adapter.connect() is True
            async with ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{unused_tcp_port}/health"
                ) as response:
                    assert response.status == 200
                    assert await response.json() == {
                        "status": "ok",
                        "platform": "webhook",
                    }
        finally:
            await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_hmac_rejects_missing_or_bad_signature_and_accepts_valid(self):
        adapter = _make_adapter(
            routes={
                "signed": {
                    "secret": "route-secret",
                    "prompt": "Message: {message}",
                    "deliver": "log",
                }
            }
        )
        events = []

        async def _handle_message(event):
            events.append(event)

        adapter.handle_message = _handle_message  # type: ignore[method-assign]
        body = _json_body({"message": "hello", "event_type": "push"})

        missing = await adapter._handle_webhook(_FakeRequest("signed", body))
        assert missing.status == 401

        bad = await adapter._handle_webhook(
            _FakeRequest(
                "signed",
                body,
                headers={"X-Hub-Signature-256": "sha256=bad"},
            )
        )
        assert bad.status == 401

        valid = await adapter._handle_webhook(
            _FakeRequest(
                "signed",
                body,
                headers={
                    "X-Hub-Signature-256": _github_signature(
                        "route-secret", body
                    ),
                    "X-GitHub-Delivery": "delivery-1",
                },
            )
        )
        assert valid.status == 202
        await asyncio.sleep(0)
        assert events[0].text == "Message: hello"
        assert events[0].source.chat_id == "webhook:signed:delivery-1"

    @pytest.mark.asyncio
    async def test_deliver_only_sends_rendered_prompt_without_agent(self):
        adapter = _make_adapter(
            routes={
                "notify": {
                    "secret": "route-secret",
                    "prompt": "Alert: {message}",
                    "deliver": "telegram",
                    "deliver_extra": {"chat_id": "chat-123"},
                    "deliver_only": True,
                }
            }
        )
        sent = []

        class _TargetAdapter:
            async def send(self, chat_id, content, reply_to=None, metadata=None):
                sent.append(
                    {
                        "chat_id": chat_id,
                        "content": content,
                        "reply_to": reply_to,
                        "metadata": metadata,
                    }
                )
                return SendResult(success=True)

        async def _unexpected_agent_run(_event):
            raise AssertionError("deliver_only must not invoke the agent")

        adapter.handle_message = _unexpected_agent_run  # type: ignore[method-assign]
        adapter.gateway_runner = SimpleNamespace(
            adapters={Platform.TELEGRAM: _TargetAdapter()},
            config=SimpleNamespace(get_home_channel=lambda _platform: None),
        )
        body = _json_body({"message": "deploy done", "event_type": "alert"})

        response = await adapter._handle_webhook(
            _FakeRequest(
                "notify",
                body,
                headers={
                    "X-Hub-Signature-256": _github_signature(
                        "route-secret", body
                    ),
                    "X-GitHub-Delivery": "delivery-2",
                },
            )
        )

        assert response.status == 200
        assert json.loads(response.text)["status"] == "delivered"
        assert sent == [
            {
                "chat_id": "chat-123",
                "content": "Alert: deploy done",
                "reply_to": None,
                "metadata": None,
            }
        ]
