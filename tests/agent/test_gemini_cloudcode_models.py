"""Tests for the google-gemini-cli / Antigravity model alias catalog.

Covers agent/gemini_cloudcode_models.py:
- Alias-key priority: picker_id > app_label > app_slug > manual spellings.
  Google's raw app slugs are shifted one tier relative to displayed labels
  (their slug ``gemini-3.5-flash-low`` means the Medium preset), so the raw
  slug must lose to the picker meaning when the keys collide.
- Regression guard: every picker_id resolves to its own backend_model.
- Manual extra spellings keep resolving after the priority restructure.
- Route hint round-trip (private extra_body key set / stripped).
"""
from __future__ import annotations

import pytest

from agent.gemini_cloudcode_models import (
    GOOGLE_GEMINI_CLI_APP_ALIASES,
    GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY,
    GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE,
    google_gemini_cli_route_from_extra_body,
    resolve_google_gemini_cli_model_alias,
    strip_google_gemini_cli_route_hint,
)


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


# =============================================================================
# Picker-id regression guard (the missing guard that let the collision land)
# =============================================================================

class TestPickerIdAlwaysWins:
    @pytest.mark.parametrize(
        "alias",
        GOOGLE_GEMINI_CLI_APP_ALIASES,
        ids=[alias.picker_id for alias in GOOGLE_GEMINI_CLI_APP_ALIASES],
    )
    def test_every_picker_id_resolves_to_its_own_backend_model(self, alias):
        backend_model, _extra = resolve_google_gemini_cli_model_alias(
            alias.picker_id
        )
        assert backend_model == alias.backend_model, (
            f"picker_id {alias.picker_id!r} must resolve to its own backend "
            f"{alias.backend_model!r}, got {backend_model!r} — a raw app slug "
            "for a different tier is shadowing the picker meaning"
        )

    def test_flash_low_picker_id_is_low_not_medium(self):
        # Google's raw slug 'gemini-3.5-flash-low' means their Medium preset;
        # the Hermes picker id 'gemini-3.5-flash-low' must stay Low (M187).
        backend_model, _extra = resolve_google_gemini_cli_model_alias(
            "gemini-3.5-flash-low"
        )
        assert backend_model == "MODEL_PLACEHOLDER_M187"

    def test_flash_medium_picker_id_resolves_medium(self):
        backend_model, _extra = resolve_google_gemini_cli_model_alias(
            "gemini-3.5-flash-medium"
        )
        assert backend_model == "MODEL_PLACEHOLDER_M20"

    def test_non_colliding_raw_slug_still_resolves_low(self):
        # The Low alias's own raw slug keeps working.
        backend_model, _extra = resolve_google_gemini_cli_model_alias(
            "gemini-3.5-flash-extra-low"
        )
        assert backend_model == "MODEL_PLACEHOLDER_M187"


# =============================================================================
# Non-colliding raw slugs and app labels
# =============================================================================

class TestRawSlugAndLabelResolution:
    def test_non_colliding_app_slugs_resolve_to_their_own_alias(self):
        picker_keys = {
            _normalize(alias.picker_id) for alias in GOOGLE_GEMINI_CLI_APP_ALIASES
        }
        checked = 0
        for alias in GOOGLE_GEMINI_CLI_APP_ALIASES:
            if not alias.app_slug:
                continue
            slug_key = _normalize(alias.app_slug)
            if slug_key in picker_keys and slug_key != _normalize(alias.picker_id):
                continue  # colliding slug intentionally loses to the picker id
            backend_model, _extra = resolve_google_gemini_cli_model_alias(
                alias.app_slug
            )
            assert backend_model == alias.backend_model, alias.app_slug
            checked += 1
        assert checked > 0

    @pytest.mark.parametrize(
        "spelling, expected_enum",
        [
            ("claude-sonnet-4-6", "MODEL_PLACEHOLDER_M35"),
            ("claude-opus-4-6-thinking", "MODEL_PLACEHOLDER_M26"),
            ("gpt-oss-120b-medium", "MODEL_OPENAI_GPT_OSS_120B_MEDIUM"),
            ("gemini-3-flash-agent", "MODEL_PLACEHOLDER_M132"),
            ("gemini-pro-agent", "MODEL_PLACEHOLDER_M16"),
        ],
    )
    def test_specific_raw_slugs(self, spelling, expected_enum):
        backend_model, _extra = resolve_google_gemini_cli_model_alias(spelling)
        assert backend_model == expected_enum

    @pytest.mark.parametrize(
        "alias",
        [a for a in GOOGLE_GEMINI_CLI_APP_ALIASES if a.app_label],
        ids=[a.picker_id for a in GOOGLE_GEMINI_CLI_APP_ALIASES if a.app_label],
    )
    def test_app_labels_resolve_to_their_own_alias(self, alias):
        backend_model, _extra = resolve_google_gemini_cli_model_alias(
            alias.app_label
        )
        assert backend_model == alias.backend_model

    def test_label_resolution_is_case_and_whitespace_insensitive(self):
        backend_model, _extra = resolve_google_gemini_cli_model_alias(
            "  gemini 3.5  FLASH (low)  "
        )
        assert backend_model == "MODEL_PLACEHOLDER_M187"


# =============================================================================
# Manual extra spellings (must survive the priority restructure)
# =============================================================================

class TestManualSpellings:
    @pytest.mark.parametrize(
        "spelling, expected_enum",
        [
            ("gemini-3-flash-preview-low", "MODEL_PLACEHOLDER_M187"),
            ("gemini-3-flash-preview-medium", "MODEL_PLACEHOLDER_M20"),
            ("gemini-3-flash-preview-high", "MODEL_PLACEHOLDER_M132"),
            ("gemini-3.1-pro-preview-low", "MODEL_PLACEHOLDER_M36"),
            ("gemini-3.1-pro-preview-high", "MODEL_PLACEHOLDER_M16"),
            ("claude-sonnet-4-6-thinking", "MODEL_PLACEHOLDER_M35"),
            ("claude-sonnet-4.6-thinking", "MODEL_PLACEHOLDER_M35"),
            ("claude-sonnet-4.6", "MODEL_PLACEHOLDER_M35"),
            ("claude-opus-4.6-thinking", "MODEL_PLACEHOLDER_M26"),
        ],
    )
    def test_manual_spelling_resolves(self, spelling, expected_enum):
        backend_model, _extra = resolve_google_gemini_cli_model_alias(spelling)
        assert backend_model == expected_enum

    def test_manual_spellings_never_shadow_catalog_keys(self):
        # Manual spellings are the lowest tier: none may steal a key that a
        # picker_id, app_label, or app_slug already claims.
        catalog_keys = set()
        for alias in GOOGLE_GEMINI_CLI_APP_ALIASES:
            catalog_keys.add(_normalize(alias.picker_id))
            if alias.app_label:
                catalog_keys.add(_normalize(alias.app_label))
            if alias.app_slug:
                catalog_keys.add(_normalize(alias.app_slug))
        from agent.gemini_cloudcode_models import _MANUAL_ALIAS_SPELLINGS

        overlap = {
            _normalize(key) for key in _MANUAL_ALIAS_SPELLINGS
        } & catalog_keys
        assert not overlap, (
            f"manual spellings collide with catalog keys: {sorted(overlap)}"
        )


# =============================================================================
# Route hint behavior (unchanged by the fix)
# =============================================================================

class TestRouteHint:
    def test_antigravity_alias_sets_private_route_key(self):
        backend_model, extra_body = resolve_google_gemini_cli_model_alias(
            "gemini-3.5-flash-low", {"caller": "kept"}
        )
        assert backend_model == "MODEL_PLACEHOLDER_M187"
        assert extra_body is not None
        assert extra_body["caller"] == "kept"
        assert (
            google_gemini_cli_route_from_extra_body(extra_body)
            == GOOGLE_GEMINI_CLI_ROUTE_ANTIGRAVITY
        )

    def test_strip_removes_private_route_key(self):
        _backend, extra_body = resolve_google_gemini_cli_model_alias(
            "gemini-3.5-flash-low"
        )
        cleaned = strip_google_gemini_cli_route_hint(extra_body)
        assert cleaned is None or all(
            not key.startswith("_google_gemini_cli") for key in cleaned
        )
        assert (
            google_gemini_cli_route_from_extra_body(cleaned)
            == GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE
        )

    def test_strip_preserves_caller_keys(self):
        _backend, extra_body = resolve_google_gemini_cli_model_alias(
            "gemini-3.5-flash-low", {"caller": "kept"}
        )
        cleaned = strip_google_gemini_cli_route_hint(extra_body)
        assert cleaned == {"caller": "kept"}

    def test_unknown_model_passes_through_untouched(self):
        backend_model, extra_body = resolve_google_gemini_cli_model_alias(
            "totally-unknown-model", {"caller": "kept"}
        )
        assert backend_model == "totally-unknown-model"
        assert extra_body == {"caller": "kept"}
        assert (
            google_gemini_cli_route_from_extra_body(extra_body)
            == GOOGLE_GEMINI_CLI_ROUTE_CLOUDCODE
        )
