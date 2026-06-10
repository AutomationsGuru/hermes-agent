from __future__ import annotations

from hermes_cli.model_governance import (
    approved_picker_models_for_provider,
    filter_models_for_approved_picker,
    filter_provider_rows_for_approved_picker,
    is_picker_provider_approved,
)


def test_default_picker_limit_can_show_all_approved_google_oauth_models():
    """Default picker calls must not truncate approved Google OAuth aliases."""
    import inspect

    from hermes_cli.model_switch import list_authenticated_providers, list_picker_providers

    approved_count = len(approved_picker_models_for_provider("google-gemini-cli"))
    assert (
        inspect.signature(list_authenticated_providers).parameters["max_models"].default
        >= approved_count
    )
    assert (
        inspect.signature(list_picker_providers).parameters["max_models"].default
        >= approved_count
    )


def test_google_oauth_aliases_are_approved_picker_models():
    assert is_picker_provider_approved("google-gemini-cli")
    assert approved_picker_models_for_provider("google-gemini-cli") == (
        "gemini-3.5-flash-low",
        "gemini-3.5-flash-medium",
        "gemini-3.5-flash-high",
        "gemini-3.1-pro-low",
        "gemini-3.1-pro-high",
        "gemini-3.1-flash-lite",
        # Namespaced so they don't shadow the real anthropic/openai catalogs.
        "antigravity-claude-sonnet-4-6",
        "antigravity-claude-opus-4-6-thinking",
        "antigravity-gpt-oss-120b-medium",
    )


def test_filter_models_keeps_only_approved_models_in_roster_order():
    models = [
        "gemini-3-pro-preview",
        "antigravity-gpt-oss-120b-medium",
        "gemini-3.1-pro-high",
        "gemini-3.5-flash-high",
        "gemini-2.5-pro",
        "gemini-3.5-flash-low",
        "antigravity-claude-sonnet-4-6",
    ]

    assert filter_models_for_approved_picker("google-gemini-cli", models) == [
        "gemini-3.5-flash-low",
        "gemini-3.5-flash-high",
        "gemini-3.1-pro-high",
        "antigravity-claude-sonnet-4-6",
        "antigravity-gpt-oss-120b-medium",
    ]


def test_filter_provider_rows_drops_unapproved_providers_and_unapproved_models():
    rows = [
        {
            "slug": "copilot",
            "name": "GitHub Copilot",
            "models": ["gpt-5.4-mini"],
            "total_models": 1,
        },
        {
            "slug": "nous",
            "name": "Nous Portal",
            "models": [
                "anthropic/claude-opus-4.8",
                "nvidia/nemotron-3-ultra:free",
                "stepfun/step-3.7-flash:free",
            ],
            "total_models": 3,
        },
        {
            "slug": "xai-oauth",
            "name": "xAI",
            "models": ["grok-4.3", "grok-imagine-image"],
            "total_models": 2,
        },
    ]

    filtered = filter_provider_rows_for_approved_picker(rows)

    assert [row["slug"] for row in filtered] == ["nous", "xai-oauth"]
    assert filtered[0]["models"] == [
        "nvidia/nemotron-3-ultra:free",
        "stepfun/step-3.7-flash:free",
    ]
    assert filtered[0]["total_models"] == 2
    assert filtered[1]["models"] == ["grok-4.3"]
    assert filtered[1]["total_models"] == 1


def test_local_ollama_picker_excludes_embedding_only_model():
    assert filter_models_for_approved_picker(
        "ollama-local",
        ["nomic-embed-text:latest", "gemma4:latest", "gpt-oss:latest"],
    ) == ["gpt-oss:latest", "gemma4:latest"]
