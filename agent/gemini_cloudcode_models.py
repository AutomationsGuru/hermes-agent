"""Model catalog and alias helpers for Google Gemini CLI / Cloud Code Assist.

Google's app surfaces some choices as product labels such as
"Gemini 3.5 Flash (High)".  A subset route through Cloud Code Assist's direct
``generateContent`` endpoint, while Antigravity-only choices require the local
Antigravity language-server bridge and its internal model enums.  This module
keeps picker IDs, backend IDs/enums, and route hints in one place so picker and
runtime stay aligned.
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


# Slug-safe picker aliases for app-visible presets.  Models marked
# ``antigravity`` are reachable through the local Antigravity language server
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
    GoogleGeminiCliModelAlias(
        picker_id="claude-sonnet-4-6",
        backend_model="MODEL_PLACEHOLDER_M35",
        app_label="Claude Sonnet 4.6 (Thinking)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="claude-sonnet-4-6",
    ),
    GoogleGeminiCliModelAlias(
        picker_id="claude-opus-4-6-thinking",
        backend_model="MODEL_PLACEHOLDER_M26",
        app_label="Claude Opus 4.6 (Thinking)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="claude-opus-4-6-thinking",
    ),
    GoogleGeminiCliModelAlias(
        picker_id="gpt-oss-120b-medium",
        backend_model="MODEL_OPENAI_GPT_OSS_120B_MEDIUM",
        app_label="GPT-OSS 120B (Medium)",
        route=GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
        app_slug="gpt-oss-120b-medium",
    ),
)

GOOGLE_GEMINI_CLI_APP_MODEL_IDS: list[str] = [
    alias.picker_id for alias in GOOGLE_GEMINI_CLI_APP_ALIASES
]

# Offline/static fallback for raw Cloud Code Assist backend IDs.  Account-
# specific discovery should prefer retrieveUserQuota buckets when available;
# these keep manual typed use working before auth or when quota endpoint is
# temporarily unreachable. Governance filters decide what is picker-visible.
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


_ALIAS_BY_KEY: dict[str, GoogleGeminiCliModelAlias] = {}
for _alias in GOOGLE_GEMINI_CLI_APP_ALIASES:
    _ALIAS_BY_KEY[_normalize_alias_key(_alias.picker_id)] = _alias
    _ALIAS_BY_KEY[_normalize_alias_key(_alias.app_label)] = _alias
    if _alias.app_slug:
        _ALIAS_BY_KEY[_normalize_alias_key(_alias.app_slug)] = _alias

# Manual spellings that should work even if they are not shown in the picker.
_ALIAS_BY_KEY.update({
    "gemini-3-flash-preview-low": GOOGLE_GEMINI_CLI_APP_ALIASES[0],
    "gemini-3-flash-preview-medium": GOOGLE_GEMINI_CLI_APP_ALIASES[1],
    "gemini-3-flash-preview-high": GOOGLE_GEMINI_CLI_APP_ALIASES[2],
    "gemini-3.1-pro-preview-low": GOOGLE_GEMINI_CLI_APP_ALIASES[3],
    "gemini-3.1-pro-preview-high": GOOGLE_GEMINI_CLI_APP_ALIASES[4],
    "claude-sonnet-4-6-thinking": GOOGLE_GEMINI_CLI_APP_ALIASES[6],
    "claude-sonnet-4.6-thinking": GOOGLE_GEMINI_CLI_APP_ALIASES[6],
    "claude-sonnet-4.6": GOOGLE_GEMINI_CLI_APP_ALIASES[6],
    "claude-opus-4.6-thinking": GOOGLE_GEMINI_CLI_APP_ALIASES[7],
})


def google_gemini_cli_model_capabilities(model: str) -> dict[str, Any]:
    """Return Hermes capability metadata for a google-gemini-cli model ID.

    Direct Cloud Code backend IDs stay full adapter models. App-visible
    Antigravity aliases are intentionally marked text-only until native
    structured chat/tool-call support is proven for the local API.
    """

    alias = _ALIAS_BY_KEY.get(_normalize_alias_key(model))
    route = alias.route if alias is not None else GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE
    if route == GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY:
        return {
            "route": GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
            "text_only": True,
            "tool_calls": False,
            "streaming": "synthetic",
            "requires_local_antigravity": True,
        }
    return {
        "route": GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE,
        "text_only": False,
        "tool_calls": True,
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
    """Build the picker list for the google-gemini-cli provider.

    App-style aliases come first, then live quota-discovered Cloud Code backend
    IDs, then raw fallback IDs for manual/offline use. Picker governance may
    further hide raw backend IDs from user-facing selection surfaces.
    """

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
    """Resolve picker/app aliases into a backend model/enum and extra_body.

    Cloud Code aliases may contribute ``thinkingLevel``.  Antigravity aliases
    carry a private route hint so the runtime calls the local language-server
    bridge instead of direct ``cloudcode-pa``.
    """

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
        # Keep one canonical spelling so downstream code does not have to merge
        # two equivalent keys.
        merged_extra_body.pop("thinkingConfig", None)

    return alias.backend_model, merged_extra_body or None
