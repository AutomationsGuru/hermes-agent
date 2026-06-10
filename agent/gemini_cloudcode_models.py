"""Model catalog and alias helpers for Google Gemini CLI / Antigravity routes.

Google's app surfaces some choices as product labels such as
"Gemini 3.5 Flash (High)". A subset route through Cloud Code Assist's direct
``generateContent`` endpoint, while Antigravity-only choices require the local
Antigravity language-server/Cascade bridge and its internal model enums. This
module keeps picker IDs, backend IDs/enums, capability metadata, and private
route hints aligned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE = "cloudcode"
GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY = "antigravity"
_GOOGLE_GEMINI_CLI_ROUTE_EXTRA_KEY = "_google_gemini_cli_route"


@dataclass(frozen=True)
class GoogleGeminiCliModelAlias:
    """A picker-visible alias resolved before sending to a backend."""

    picker_id: str
    backend_model: str
    thinking_level: str = ""
    app_label: str = ""
    route: str = GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE
    app_slug: str = ""


# Slug-safe picker aliases for app-visible presets. Models marked antigravity
# are reachable through the local Antigravity language server/Cascade bridge
# with its internal enum, not through direct cloudcode-pa model slugs.
GOOGLE_GEMINI_CLI_APP_ALIASES: tuple[GoogleGeminiCliModelAlias, ...] = (
    GoogleGeminiCliModelAlias(
        picker_id="gemini-3.5-flash-low",
        backend_model="MODEL_PLACEHOLDER_M187",
        app_label="Gemini 3.5 Flash (Low)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="gemini-3.5-flash-extra-low",
    ),
    GoogleGeminiCliModelAlias(
        picker_id="gemini-3.5-flash-medium",
        backend_model="MODEL_PLACEHOLDER_M20",
        app_label="Gemini 3.5 Flash (Medium)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="gemini-3.5-flash-low",
    ),
    GoogleGeminiCliModelAlias(
        picker_id="gemini-3.5-flash-high",
        backend_model="MODEL_PLACEHOLDER_M132",
        app_label="Gemini 3.5 Flash (High)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="gemini-3-flash-agent",
    ),
    GoogleGeminiCliModelAlias(
        picker_id="gemini-3.1-pro-low",
        backend_model="MODEL_PLACEHOLDER_M36",
        app_label="Gemini 3.1 Pro (Low)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="gemini-3.1-pro-low",
    ),
    GoogleGeminiCliModelAlias(
        picker_id="gemini-3.1-pro-high",
        backend_model="MODEL_PLACEHOLDER_M16",
        app_label="Gemini 3.1 Pro (High)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="gemini-pro-agent",
    ),
    GoogleGeminiCliModelAlias(
        picker_id="gemini-3.1-flash-lite",
        backend_model="MODEL_PLACEHOLDER_M50",
        app_label="Gemini 3.1 Flash Lite",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="gemini-3.1-flash-lite",
    ),
    # Cross-vendor presets are namespaced with an ``antigravity-`` prefix so
    # the curated google-gemini-cli list never shadows the real anthropic /
    # openai catalogs in provider auto-detection (e.g. the short aliases
    # ``sonnet``/``opus``/``gpt`` must keep resolving to their own vendors).
    GoogleGeminiCliModelAlias(
        picker_id="antigravity-claude-sonnet-4-6",
        backend_model="MODEL_PLACEHOLDER_M35",
        app_label="Claude Sonnet 4.6 (Thinking)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="claude-sonnet-4-6",
    ),
    GoogleGeminiCliModelAlias(
        picker_id="antigravity-claude-opus-4-6-thinking",
        backend_model="MODEL_PLACEHOLDER_M26",
        app_label="Claude Opus 4.6 (Thinking)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="claude-opus-4-6-thinking",
    ),
    GoogleGeminiCliModelAlias(
        picker_id="antigravity-gpt-oss-120b-medium",
        backend_model="MODEL_OPENAI_GPT_OSS_120B_MEDIUM",
        app_label="GPT-OSS 120B (Medium)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="gpt-oss-120b-medium",
    ),
)

GOOGLE_GEMINI_CLI_APP_MODEL_IDS: list[str] = [
    alias.picker_id for alias in GOOGLE_GEMINI_CLI_APP_ALIASES
]

# Offline/static fallback for raw Cloud Code Assist backend IDs. Account-specific
# discovery should prefer retrieveUserQuota buckets when available; these keep
# manual typed use working before auth or when quota endpoint is unavailable.
GOOGLE_GEMINI_CLI_RAW_FALLBACK_MODELS: list[str] = [
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
]

GOOGLE_GEMINI_CLI_CURATED_MODELS: list[str] = [
    *GOOGLE_GEMINI_CLI_APP_MODEL_IDS,
    *GOOGLE_GEMINI_CLI_RAW_FALLBACK_MODELS,
]


def _normalize_alias_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


# Key registration runs in explicit priority passes (lowest first, so later
# passes overwrite): app_slug < app_label < picker_id. A picker_id shown in
# the Hermes picker must ALWAYS resolve to its own backend_model, even when
# Google's raw slug for a *different* tier collides with it. Google's app
# slugs are shifted one tier relative to displayed labels — their raw slug
# 'gemini-3.5-flash-low' actually means the Medium preset — so that colliding
# raw slug intentionally loses to the picker meaning of 'gemini-3.5-flash-low'
# (the Low alias). Non-colliding raw slugs (e.g. 'gemini-3.5-flash-extra-low')
# still resolve to their own alias.
_ALIAS_BY_KEY: dict[str, GoogleGeminiCliModelAlias] = {}
for _alias in GOOGLE_GEMINI_CLI_APP_ALIASES:
    if _alias.app_slug:
        _ALIAS_BY_KEY[_normalize_alias_key(_alias.app_slug)] = _alias
for _alias in GOOGLE_GEMINI_CLI_APP_ALIASES:
    if _alias.app_label:
        _ALIAS_BY_KEY[_normalize_alias_key(_alias.app_label)] = _alias
for _alias in GOOGLE_GEMINI_CLI_APP_ALIASES:
    _ALIAS_BY_KEY[_normalize_alias_key(_alias.picker_id)] = _alias

_ALIAS_BY_PICKER_ID: dict[str, GoogleGeminiCliModelAlias] = {
    _alias.picker_id: _alias for _alias in GOOGLE_GEMINI_CLI_APP_ALIASES
}

# Manual spellings that should work even if they are not shown in the picker.
# Lowest priority: they only fill keys not already claimed by a picker_id,
# app_label, or app_slug above. Referenced by picker_id (not list position)
# so reordering GOOGLE_GEMINI_CLI_APP_ALIASES cannot silently remap them.
_MANUAL_ALIAS_SPELLINGS: dict[str, str] = {
    "gemini-3-flash-preview-low": "gemini-3.5-flash-low",
    "gemini-3-flash-preview-medium": "gemini-3.5-flash-medium",
    "gemini-3-flash-preview-high": "gemini-3.5-flash-high",
    "gemini-3.1-pro-preview-low": "gemini-3.1-pro-low",
    "gemini-3.1-pro-preview-high": "gemini-3.1-pro-high",
    "claude-sonnet-4-6-thinking": "antigravity-claude-sonnet-4-6",
    "claude-sonnet-4.6-thinking": "antigravity-claude-sonnet-4-6",
    "claude-sonnet-4.6": "antigravity-claude-sonnet-4-6",
    "claude-opus-4.6-thinking": "antigravity-claude-opus-4-6-thinking",
}
for _manual_key, _picker_id in _MANUAL_ALIAS_SPELLINGS.items():
    _ALIAS_BY_KEY.setdefault(
        _normalize_alias_key(_manual_key), _ALIAS_BY_PICKER_ID[_picker_id]
    )


_ANTIGRAVITY_BACKEND_MODELS: frozenset[str] = frozenset(
    alias.backend_model
    for alias in GOOGLE_GEMINI_CLI_APP_ALIASES
    if alias.route == GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY
)

# SINGLE SOURCE OF TRUTH for which Antigravity backend enums support Hermes-side
# (Option A) tool calling. The adapter's runtime tool gate AND
# google_gemini_cli_model_capabilities() below both read these, so the
# advertised capability can never drift from what the adapter enforces.
#
# As of 2026-06-10, only GPT-OSS-120B reliably emits prompt-injected tool calls
# over the Cascade route. The Claude models, wrapped in Antigravity's own agent
# persona, do NOT (Opus answers without calling; Sonnet deliberates ~60s and
# truncates), so they are tool-gated OFF and remain chat-only. Gemini presets
# are likewise not tool-enabled here (use the normal google provider for tools).
# Re-enable a model here ONLY after live re-verification.
ANTIGRAVITY_TOOL_ENABLED_ENUMS: frozenset[str] = frozenset({
    "MODEL_OPENAI_GPT_OSS_120B_MEDIUM",  # antigravity-gpt-oss-120b-medium
})
# Cross-vendor models that are chat-capable but NOT reliable for tools — used to
# give a clear, specific "chat-only" error/marker instead of a generic one.
ANTIGRAVITY_TOOL_UNRELIABLE_ENUMS: frozenset[str] = frozenset({
    "MODEL_PLACEHOLDER_M35",  # antigravity-claude-sonnet-4-6 (Thinking)
    "MODEL_PLACEHOLDER_M26",  # antigravity-claude-opus-4-6-thinking
})


def is_antigravity_backend_model(model: str) -> bool:
    """Return whether ``model`` is a curated Antigravity backend enum.

    Used to gate the private antigravity route hint: a caller-supplied hint must
    never reroute an arbitrary model (e.g. a Cloud Code slug) to Cascade — only
    enums that alias resolution actually produces for an Antigravity-routed
    picker entry are eligible.
    """

    return str(model or "").strip() in _ANTIGRAVITY_BACKEND_MODELS


def google_gemini_cli_model_capabilities(model: str) -> dict[str, Any]:
    """Return Hermes capability metadata for a google-gemini-cli model ID."""

    alias = _ALIAS_BY_KEY.get(_normalize_alias_key(model))
    route = alias.route if alias is not None else GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE
    if route == GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY:
        # Per-model tool support, read from the same set the adapter's runtime
        # gate enforces — so capabilities never over-promise. Only GPT-OSS is
        # tool-capable today; the Claude presets are chat-only (see the set's
        # comment). The Cascade route flattens input to text (text_only).
        backend = alias.backend_model if alias is not None else ""
        tools_ok = backend in ANTIGRAVITY_TOOL_ENABLED_ENUMS
        return {
            "route": GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
            "text_only": True,
            "tool_calls": tools_ok,
            "chat_only": not tools_ok,
            "tool_calls_models": ("openai",) if tools_ok else (),
            "observed_workspace_tools": True,
            "auto_execute_tools": False,
            "auto_ack_edits": False,
            "streaming": "openai_chunks",
            "requires_local_antigravity": True,
        }
    return {
        "route": GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE,
        "text_only": False,
        "tool_calls": True,
        "chat_only": False,
        "observed_workspace_tools": False,
        "auto_execute_tools": True,
        "auto_ack_edits": False,
        "streaming": "native",
        "requires_local_antigravity": False,
    }


def dedupe_google_gemini_cli_models(model_ids: Iterable[str]) -> list[str]:
    """Return model IDs de-duplicated case-insensitively, preserving order."""

    seen: set[str] = set()
    out: list[str] = []
    for model_id in model_ids:
        value = str(model_id or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def build_google_gemini_cli_picker_models(
    live_model_ids: Iterable[str] | None = None,
) -> list[str]:
    """Build the picker list for the google-gemini-cli provider."""

    return dedupe_google_gemini_cli_models([
        *GOOGLE_GEMINI_CLI_APP_MODEL_IDS,
        *(live_model_ids or []),
        *GOOGLE_GEMINI_CLI_RAW_FALLBACK_MODELS,
    ])


def google_gemini_cli_route_from_extra_body(extra_body: dict[str, Any] | None) -> str:
    """Return the internal route hint carried in ``extra_body`` if present."""

    if not isinstance(extra_body, dict):
        return GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE
    route = (
        str(extra_body.get(_GOOGLE_GEMINI_CLI_ROUTE_EXTRA_KEY) or "").strip().lower()
    )
    return route or GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE


def strip_google_gemini_cli_route_hint(
    extra_body: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Remove Hermes-private route metadata before sending provider payloads."""

    if not isinstance(extra_body, dict):
        return extra_body
    cleaned = dict(extra_body)
    cleaned.pop(_GOOGLE_GEMINI_CLI_ROUTE_EXTRA_KEY, None)
    return cleaned or None


def resolve_google_gemini_cli_model_alias(
    model: str,
    extra_body: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Resolve picker/app aliases into a backend model/enum and extra_body."""

    alias = _ALIAS_BY_KEY.get(_normalize_alias_key(model))
    if alias is None:
        return model, extra_body

    merged_extra_body: dict[str, Any] = dict(extra_body or {})
    if alias.route == GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY:
        merged_extra_body[_GOOGLE_GEMINI_CLI_ROUTE_EXTRA_KEY] = (
            GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY
        )
    if alias.thinking_level:
        existing = merged_extra_body.get("thinking_config")
        if existing is None:
            existing = merged_extra_body.get("thinkingConfig")
        thinking_config = dict(existing) if isinstance(existing, dict) else {}
        thinking_config.setdefault("thinkingLevel", alias.thinking_level)
        merged_extra_body["thinking_config"] = thinking_config
        merged_extra_body.pop("thinkingConfig", None)

    return alias.backend_model, merged_extra_body or None
