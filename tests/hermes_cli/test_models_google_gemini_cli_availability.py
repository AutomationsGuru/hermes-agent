"""google-gemini-cli model listing/selection vs local Antigravity availability.

The curated google-gemini-cli catalog mixes two routes:

- cloudcode-route slugs (``gemini-3.1-pro-preview``, ``gemini-3-flash-preview``,
  the GA ``gemini-3.5-flash``) served by Google's cloudcode-pa endpoint, and
- antigravity-route ids (the app presets from
  ``GOOGLE_GEMINI_CLI_APP_MODEL_IDS`` plus the experimental
  ``antigravity-cascade``) that only answer while the local Antigravity
  language server is running.

These tests fake ``agent.google_antigravity_bridge.check_antigravity_available``
for both states and assert the hermes_cli surfaces degrade honestly: hidden
(never erroring) listings when down, untouched cloudcode entries, actionable
selection messages, and at most one local probe per listing call.
"""

from types import SimpleNamespace

import pytest

import hermes_cli.models as models_mod
from agent.gemini_cloudcode_models import GOOGLE_GEMINI_CLI_APP_MODEL_IDS
from hermes_cli.models import (
    _PROVIDER_MODELS,
    filter_unavailable_google_gemini_cli_models,
    get_default_model_for_provider,
    google_gemini_cli_antigravity_status,
    provider_model_ids,
    validate_requested_model,
)

CLOUDCODE_IDS = [
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
]
ANTIGRAVITY_IDS = [*GOOGLE_GEMINI_CLI_APP_MODEL_IDS, "antigravity-cascade"]


class _ProbeFake:
    """Counting stand-in for agent.google_antigravity_bridge.check_antigravity_available."""

    def __init__(self, available, *, raise_exc=False):
        self.available = available
        self.raise_exc = raise_exc
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError("probe blew up (must never escape)")
        if self.available:
            return SimpleNamespace(
                available=True, base_url="https://127.0.0.1:51520", reason=""
            )
        return SimpleNamespace(
            available=False,
            base_url="",
            reason=(
                "Antigravity bridge unavailable. Start Antigravity or set "
                "HERMES_ANTIGRAVITY_URL."
            ),
        )


@pytest.fixture
def fake_probe(monkeypatch):
    """Install a controllable availability probe and reset the process memo."""

    def _install(available, *, raise_exc=False):
        probe = _ProbeFake(available, raise_exc=raise_exc)
        monkeypatch.setattr(
            "agent.google_antigravity_bridge.check_antigravity_available",
            probe,
        )
        monkeypatch.setattr(models_mod, "_antigravity_status_cache", None)
        return probe

    return _install


# ---------------------------------------------------------------------------
# Curated list contents (Task 1 regression guards)
# ---------------------------------------------------------------------------


class TestCuratedList:
    def test_ga_flash_slug_restored(self):
        """'gemini-3.5-flash' is the GA-channel direct cloudcode-pa slug and
        must stay in the curated list — it works without Antigravity."""
        assert "gemini-3.5-flash" in _PROVIDER_MODELS["google-gemini-cli"]

    def test_cloudcode_and_antigravity_entries_present(self):
        models = _PROVIDER_MODELS["google-gemini-cli"]
        for model_id in CLOUDCODE_IDS + ANTIGRAVITY_IDS:
            assert model_id in models, model_id

    def test_route_metadata_split(self):
        for model_id in ANTIGRAVITY_IDS:
            assert models_mod._google_gemini_cli_model_requires_antigravity(
                model_id
            ), model_id
        for model_id in CLOUDCODE_IDS:
            assert not models_mod._google_gemini_cli_model_requires_antigravity(
                model_id
            ), model_id

    def test_silent_default_is_cloudcode_route(self, fake_probe):
        """The non-interactive fallback must never depend on the local app
        (and must stay network-free: no probe)."""
        probe = fake_probe(False)
        default = get_default_model_for_provider("google-gemini-cli")
        assert default == "gemini-3.1-pro-preview"
        assert not models_mod._google_gemini_cli_model_requires_antigravity(default)
        assert probe.calls == 0


# ---------------------------------------------------------------------------
# Listing: provider_model_ids / cached_provider_model_ids
# ---------------------------------------------------------------------------


class TestListingAvailability:
    def test_up_state_lists_everything(self, fake_probe):
        fake_probe(True)
        ids = provider_model_ids("google-gemini-cli")
        # Invariant: when the local server is up nothing is filtered, so every
        # static catalog entry remains selectable. Asserted as a subset (not
        # exact equality/order) so live quota-discovered models can be unioned
        # in without breaking this test.
        assert set(_PROVIDER_MODELS["google-gemini-cli"]).issubset(set(ids))
        for model_id in ANTIGRAVITY_IDS:
            assert model_id in ids, model_id

    def test_down_state_hides_antigravity_entries_only(self, fake_probe):
        fake_probe(False)
        ids = provider_model_ids("google-gemini-cli")
        assert ids == CLOUDCODE_IDS  # cloudcode entries untouched, in order
        for model_id in ANTIGRAVITY_IDS:
            assert model_id not in ids, model_id

    def test_down_state_never_raises_even_if_probe_raises(self, fake_probe):
        fake_probe(False, raise_exc=True)
        ids = provider_model_ids("google-gemini-cli")
        assert ids == CLOUDCODE_IDS

    def test_at_most_one_probe_per_listing_call(self, fake_probe):
        probe = fake_probe(True)
        provider_model_ids("google-gemini-cli")
        assert probe.calls == 1
        # Memoized: a second listing inside the TTL does not re-probe.
        provider_model_ids("google-gemini-cli")
        assert probe.calls == 1

    def test_force_refresh_bypasses_memo(self, fake_probe):
        probe = fake_probe(True)
        provider_model_ids("google-gemini-cli")
        provider_model_ids("google-gemini-cli", force_refresh=True)
        assert probe.calls == 2

    def test_filter_skips_probe_for_pure_cloudcode_lists(self, fake_probe):
        probe = fake_probe(False)
        ids = filter_unavailable_google_gemini_cli_models(CLOUDCODE_IDS)
        assert ids == CLOUDCODE_IDS
        assert probe.calls == 0

    def test_cached_wrapper_bypasses_disk_cache(self, fake_probe, monkeypatch):
        """Availability snapshots must not get pinned in the 1h disk cache:
        the wrapper computes google-gemini-cli live and never reads/writes."""
        loads, saves = [], []
        monkeypatch.setattr(
            models_mod, "_load_provider_models_cache",
            lambda: loads.append(1) or {},
        )
        monkeypatch.setattr(
            models_mod, "_save_provider_models_cache",
            lambda data: saves.append(data),
        )

        fake_probe(False)
        down_ids = models_mod.cached_provider_model_ids("google-gemini-cli")
        assert down_ids == CLOUDCODE_IDS

        fake_probe(True)  # app comes up: next listing recovers immediately
        up_ids = models_mod.cached_provider_model_ids("google-gemini-cli")
        assert up_ids == list(_PROVIDER_MODELS["google-gemini-cli"])

        assert not loads and not saves

    def test_curated_models_for_provider_down_state(self, fake_probe, monkeypatch):
        fake_probe(False)
        pairs = models_mod.curated_models_for_provider("google-gemini-cli")
        ids = [model_id for model_id, _desc in pairs]
        assert ids == CLOUDCODE_IDS


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------


class TestStatusHelper:
    def test_up_status_shape(self, fake_probe):
        fake_probe(True)
        status = google_gemini_cli_antigravity_status()
        assert status.available is True
        assert status.base_url.startswith("https://127.0.0.1:")

    def test_down_status_reason_is_actionable(self, fake_probe):
        fake_probe(False)
        status = google_gemini_cli_antigravity_status()
        assert status.available is False
        assert "Start Antigravity or set HERMES_ANTIGRAVITY_URL" in status.reason

    def test_probe_exception_degrades_to_unavailable(self, fake_probe):
        fake_probe(True, raise_exc=True)
        status = google_gemini_cli_antigravity_status()
        assert status.available is False
        assert "Start Antigravity or set HERMES_ANTIGRAVITY_URL" in status.reason

    def test_memo_serves_repeat_calls(self, fake_probe):
        probe = fake_probe(True)
        first = google_gemini_cli_antigravity_status()
        second = google_gemini_cli_antigravity_status()
        assert first == second
        assert probe.calls == 1


# ---------------------------------------------------------------------------
# Selection: validate_requested_model
# ---------------------------------------------------------------------------


class TestSelectionValidation:
    @pytest.mark.parametrize(
        "model_id", ["antigravity-cascade", "gemini-3.5-flash-high"]
    )
    def test_antigravity_model_down_gets_actionable_message(
        self, fake_probe, model_id
    ):
        fake_probe(False)
        result = validate_requested_model(model_id, "google-gemini-cli")
        assert result["accepted"] is True  # persists; works once the app is up
        assert result["recognized"] is True
        assert "Start Antigravity or set HERMES_ANTIGRAVITY_URL" in result["message"]

    def test_antigravity_model_up_validates_clean(self, fake_probe):
        fake_probe(True)
        result = validate_requested_model(
            "antigravity-cascade", "google-gemini-cli"
        )
        assert result == {
            "accepted": True,
            "persist": True,
            "recognized": True,
            "message": None,
        }

    def test_cloudcode_model_unaffected_by_down_state(self, fake_probe):
        """GA flash stays selectable with no Antigravity warning while the
        local app is down (no base_url → no live probe → catalog fallback)."""
        fake_probe(False)
        result = validate_requested_model("gemini-3.5-flash", "google-gemini-cli")
        assert result["accepted"] is True
        assert result["recognized"] is True
        assert result["message"] is None

    def test_validation_never_raises_when_probe_raises(self, fake_probe):
        fake_probe(False, raise_exc=True)
        result = validate_requested_model(
            "gemini-3.1-pro-high", "google-gemini-cli"
        )
        assert result["accepted"] is True
        assert "Start Antigravity or set HERMES_ANTIGRAVITY_URL" in result["message"]


# ---------------------------------------------------------------------------
# Interactive setup-flow picker must apply the same availability filter as the
# gateway/model picker (regression guard for the model_setup_flows wiring).
# ---------------------------------------------------------------------------


class TestSetupFlowPicker:
    def test_setup_flow_hides_antigravity_models_when_down(
        self, fake_probe, monkeypatch
    ):
        fake_probe(False)  # Antigravity server unreachable
        import hermes_cli.auth as auth_mod
        from hermes_cli.model_setup_flows import _model_flow_google_gemini_cli

        captured: dict[str, list[str]] = {}

        monkeypatch.setattr("builtins.input", lambda *_a, **_k: "y")
        monkeypatch.setattr(
            auth_mod, "get_gemini_oauth_auth_status", lambda: {"logged_in": True}
        )
        monkeypatch.setattr(
            auth_mod,
            "resolve_gemini_oauth_runtime_credentials",
            lambda **_k: {"project_id": ""},
        )

        def _capture(models, **_kwargs):
            captured["models"] = list(models)
            return None  # no selection -> no _save_model_choice / config write

        monkeypatch.setattr(auth_mod, "_prompt_model_selection", _capture)

        _model_flow_google_gemini_cli(None)

        assert captured.get("models"), "picker should receive a model list"
        assert not any(m in captured["models"] for m in ANTIGRAVITY_IDS), (
            "Antigravity-route models must be hidden when the local server is down"
        )
        # GA-channel cloudcode model is unaffected and still offered.
        assert "gemini-3.5-flash" in captured["models"]

    def test_setup_flow_lists_antigravity_models_when_up(
        self, fake_probe, monkeypatch
    ):
        fake_probe(True)  # Antigravity server reachable
        import hermes_cli.auth as auth_mod
        from hermes_cli.model_setup_flows import _model_flow_google_gemini_cli

        captured: dict[str, list[str]] = {}

        monkeypatch.setattr("builtins.input", lambda *_a, **_k: "y")
        monkeypatch.setattr(
            auth_mod, "get_gemini_oauth_auth_status", lambda: {"logged_in": True}
        )
        monkeypatch.setattr(
            auth_mod,
            "resolve_gemini_oauth_runtime_credentials",
            lambda **_k: {"project_id": ""},
        )

        def _capture(models, **_kwargs):
            captured["models"] = list(models)
            return None

        monkeypatch.setattr(auth_mod, "_prompt_model_selection", _capture)

        _model_flow_google_gemini_cli(None)

        assert "antigravity-claude-sonnet-4-6" in captured["models"]
        assert "antigravity-gpt-oss-120b-medium" in captured["models"]
