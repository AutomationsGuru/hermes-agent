from __future__ import annotations

from types import SimpleNamespace


def test_google_gemini_cli_alias_resolves_to_antigravity_enum_and_route_hint():
    from agent.gemini_cloudcode_models import (
        GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        google_gemini_cli_route_from_extra_body,
        resolve_google_gemini_cli_model_alias,
        strip_google_gemini_cli_route_hint,
    )

    backend_model, extra_body = resolve_google_gemini_cli_model_alias(
        "gemini-3.5-flash-high",
        {"caller": "kept"},
    )

    assert backend_model == "MODEL_PLACEHOLDER_M132"
    assert (
        google_gemini_cli_route_from_extra_body(extra_body)
        == GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY
    )
    assert strip_google_gemini_cli_route_hint(extra_body) == {"caller": "kept"}


def test_google_gemini_cli_alias_accepts_antigravity_app_slugs_and_labels():
    from agent.gemini_cloudcode_models import resolve_google_gemini_cli_model_alias

    assert (
        resolve_google_gemini_cli_model_alias("Gemini 3.5 Flash (Medium)")[0]
        == "MODEL_PLACEHOLDER_M20"
    )
    assert (
        resolve_google_gemini_cli_model_alias("gemini-3-flash-agent")[0]
        == "MODEL_PLACEHOLDER_M132"
    )
    assert (
        resolve_google_gemini_cli_model_alias("claude-sonnet-4.6-thinking")[0]
        == "MODEL_PLACEHOLDER_M35"
    )


def test_google_gemini_cli_antigravity_capabilities_are_text_only():
    from agent.gemini_cloudcode_models import google_gemini_cli_model_capabilities

    caps = google_gemini_cli_model_capabilities("gemini-3.5-flash-high")

    assert caps == {
        "route": "antigravity",
        "text_only": True,
        "tool_calls": False,
        "streaming": "synthetic",
        "requires_local_antigravity": True,
    }
    assert google_gemini_cli_model_capabilities("gemini-3-flash-preview") == {
        "route": "cloudcode",
        "text_only": False,
        "tool_calls": True,
        "streaming": "native",
        "requires_local_antigravity": False,
    }


def test_google_gemini_cli_picker_merges_app_aliases_live_quota_and_fallbacks():
    from agent.gemini_cloudcode_models import build_google_gemini_cli_picker_models

    models = build_google_gemini_cli_picker_models([
        "gemini-2.5-flash-lite",
        "gemini-3-flash-preview",
    ])

    assert models[:9] == [
        "gemini-3.5-flash-low",
        "gemini-3.5-flash-medium",
        "gemini-3.5-flash-high",
        "gemini-3.1-pro-low",
        "gemini-3.1-pro-high",
        "gemini-3.1-flash-lite",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    ]
    assert "gemini-2.5-flash-lite" in models
    assert models.count("gemini-3-flash-preview") == 1
    # ``gemini-3.5-flash`` is app-visible, but direct cloudcode-pa slug smokes
    # returned 404; the picker exposes routed aliases instead.
    assert "gemini-3.5-flash" not in models


def test_provider_model_ids_google_gemini_cli_uses_quota_buckets(monkeypatch):
    from hermes_cli import models as model_catalog

    monkeypatch.setattr(
        model_catalog,
        "_fetch_google_gemini_cli_quota_model_ids",
        lambda: ["gemini-2.5-flash-lite"],
    )

    models = model_catalog.provider_model_ids("google-gemini-cli")

    assert "gemini-3.5-flash-low" in models
    assert "gemini-3.1-pro-high" in models
    assert "claude-sonnet-4-6" in models
    assert "gpt-oss-120b-medium" in models
    assert "gemini-2.5-flash-lite" in models
    assert "gemini-3-flash-preview" in models


def test_cloudcode_client_routes_antigravity_alias_without_google_oauth(monkeypatch):
    from agent import gemini_cloudcode_adapter as adapter

    called = {}

    def fake_get_model_response(*, model_enum, prompt, **_kwargs):
        called["model_enum"] = model_enum
        called["prompt"] = prompt
        return "OK"

    monkeypatch.setattr(
        adapter.google_oauth,
        "get_valid_access_token",
        lambda: (_ for _ in ()).throw(
            AssertionError("direct Google OAuth should not be used")
        ),
    )
    monkeypatch.setattr(
        adapter.google_antigravity_bridge, "get_model_response", fake_get_model_response
    )

    client = adapter.GeminiCloudCodeClient()
    response = client.chat.completions.create(
        model="gemini-3.5-flash-high",
        messages=[{"role": "user", "content": "Return OK"}],
        max_tokens=16,
    )

    assert called["model_enum"] == "MODEL_PLACEHOLDER_M132"
    assert "User: Return OK" in called["prompt"]
    assert response.model == "gemini-3.5-flash-high"
    assert response.choices[0].message.content == "OK"


def test_cloudcode_client_keeps_raw_backend_ids_on_direct_cloudcode_path(monkeypatch):
    from agent import gemini_cloudcode_adapter as adapter

    class FakeResponse:
        status_code = 200
        headers = {}
        text = ""

        def json(self):
            return {
                "response": {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "OK"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 1,
                        "candidatesTokenCount": 1,
                        "totalTokenCount": 2,
                    },
                }
            }

    class FakeHTTP:
        def __init__(self):
            self.payload = None
            self.headers = None

        def post(self, _url, *, json, headers):
            self.payload = json
            self.headers = headers
            return FakeResponse()

    monkeypatch.setattr(adapter.google_oauth, "get_valid_access_token", lambda: "token")

    client = adapter.GeminiCloudCodeClient()
    fake_http = FakeHTTP()
    client._http = fake_http
    client._ensure_project_context = lambda _token, _model: SimpleNamespace(
        project_id="proj"
    )

    response = client.chat.completions.create(
        model="gemini-3-flash-preview",
        messages=[{"role": "user", "content": "Return OK"}],
        max_tokens=16,
    )

    assert fake_http.payload["model"] == "gemini-3-flash-preview"
    assert response.model == "gemini-3-flash-preview"
    assert response.choices[0].message.content == "OK"
