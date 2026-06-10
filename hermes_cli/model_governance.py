"""Approved provider/model filtering for selection surfaces.

This module is intentionally about *picker visibility*, not hard runtime
lockout.  Matthew's Agent OS policy distinguishes approved roster entries from
provider catalogs: available/cache-visible does not mean approved.  The helpers
below keep interactive model pickers focused on approved chat/agent routes while
leaving typed/manual runtime paths to the normal validators unless strict
allowlist enforcement is separately requested.
"""

from __future__ import annotations

from typing import Iterable


# Chat/agent picker policy.  Service-only rows from the approved roster
# (TTS/image/video/embedding-only) are deliberately excluded from this chat
# model picker allowlist.
APPROVED_PICKER_MODELS: dict[str, tuple[str, ...]] = {
    "anthropic": (
        "claude-opus-4-8",
        "claude-sonnet-4-6",
    ),
    "gemini": (
        "gemini-3.1-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-2.5-flash-lite",
    ),
    # Google OAuth / Gemini CLI / Cloud Code Assist app aliases.  These are
    # picker-visible aliases that resolve to either direct Cloud Code backend
    # IDs or Antigravity-local internal enums in agent.gemini_cloudcode_models.
    "google-gemini-cli": (
        "gemini-3.5-flash-low",
        "gemini-3.5-flash-medium",
        "gemini-3.5-flash-high",
        "gemini-3.1-pro-low",
        "gemini-3.1-pro-high",
        "gemini-3.1-flash-lite",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    ),
    "minimax": (
        "MiniMax-M3",
        "MiniMax-M2.7",
    ),
    "minimax-oauth": (
        "MiniMax-M3",
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
    ),
    "nous": (
        "nvidia/nemotron-3-ultra:free",
        "stepfun/step-3.7-flash:free",
    ),
    "ollama-cloud": (
        "gpt-oss:120b",
    ),
    # Current picker slug for the configured local Ollama custom provider.
    "ollama-local": (
        "lfm2.5:latest",
        "gpt-oss:latest",
        "cogito:latest",
        "deepseek-r1:latest",
        "phi4-mini:latest",
        "phi4:latest",
        "gemma4:latest",
    ),
    # Canonical route spelling used in docs / manual commands.
    "custom:ollama-local": (
        "lfm2.5:latest",
        "gpt-oss:latest",
        "cogito:latest",
        "deepseek-r1:latest",
        "phi4-mini:latest",
        "phi4:latest",
        "gemma4:latest",
    ),
    "openai-codex": (
        "gpt-5.5",
        "gpt-5.4-mini",
        "gpt-5.3-codex-spark",
    ),
    "openai-api": (
        "gpt-5.5-pro",
        "gpt-5.3-codex",
    ),
    "openrouter": (
        "nvidia/nemotron-3-super-120b-a12b:free",
    ),
    "xai-oauth": (
        "grok-4.3",
        "grok-4.20-0309-non-reasoning",
        "grok-4.20-0309-reasoning",
        "grok-4.20-multi-agent-0309",
        "grok-build-0.1",
    ),
}

_PROVIDER_ALIASES: dict[str, str] = {
    "custom:ollama": "ollama-local",
    "ollama": "ollama-local",
    "local-ollama": "ollama-local",
}


APPROVED_PICKER_PROVIDER_SLUGS: frozenset[str] = frozenset(APPROVED_PICKER_MODELS)


def normalize_picker_provider_slug(provider: str) -> str:
    """Normalize a provider slug for approved-picker lookup."""

    slug = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(slug, slug)


def is_picker_provider_approved(provider: str) -> bool:
    return normalize_picker_provider_slug(provider) in APPROVED_PICKER_MODELS


def approved_picker_models_for_provider(provider: str) -> tuple[str, ...]:
    return APPROVED_PICKER_MODELS.get(normalize_picker_provider_slug(provider), ())


def filter_models_for_approved_picker(
    provider: str,
    models: Iterable[str] | None,
) -> list[str]:
    """Return the provider's visible models intersected with the approved roster.

    The result follows approved-roster order, not provider-catalog order, so the
    picker remains stable even when live endpoints return hundreds of models in
    arbitrary order.
    """

    approved = approved_picker_models_for_provider(provider)
    if not approved:
        return []
    available = {str(model).lower(): str(model) for model in (models or []) if str(model or "").strip()}
    filtered: list[str] = []
    for model in approved:
        actual = available.get(model.lower())
        if actual:
            filtered.append(actual)
    return filtered


def filter_provider_rows_for_approved_picker(
    rows: Iterable[dict],
    *,
    max_models: int | None = None,
) -> list[dict]:
    """Filter provider rows down to approved picker providers/models.

    Drops unapproved providers and providers with no approved visible chat
    models.  Returns copied row dicts so callers can safely keep using their
    original row list for non-picker inventory/debug paths.
    """

    filtered_rows: list[dict] = []
    for row in rows:
        slug = str(row.get("slug") or "")
        if not is_picker_provider_approved(slug):
            continue
        models = filter_models_for_approved_picker(slug, row.get("models") or [])
        if not models:
            continue
        if max_models is not None:
            models = models[:max_models]
        out = dict(row)
        out["models"] = models
        out["total_models"] = len(models)
        filtered_rows.append(out)
    return filtered_rows
