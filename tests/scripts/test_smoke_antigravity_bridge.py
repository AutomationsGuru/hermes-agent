from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_smoke_module():
    path = (
        Path(__file__).resolve().parents[2] / "scripts" / "smoke_antigravity_bridge.py"
    )
    spec = importlib.util.spec_from_file_location("smoke_antigravity_bridge", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, *, model, messages, max_tokens):
        self.calls.append((model, messages, max_tokens))
        if model == "bad-model":
            raise RuntimeError("csrf-secret [WinError 10061] refused")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK."))]
        )


class FakeClient:
    def __init__(self):
        self.completions = FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def test_smoke_script_models_match_approved_google_roster():
    from hermes_cli.model_governance import approved_picker_models_for_provider

    smoke = _load_smoke_module()

    assert tuple(
        smoke.APPROVED_ANTIGRAVITY_MODELS
    ) == approved_picker_models_for_provider("google-gemini-cli")


def test_run_smoke_uses_safe_prompt_and_sanitizes_failures():
    smoke = _load_smoke_module()
    client = FakeClient()

    results = smoke.run_smoke(["ok-model", "bad-model"], client=client)

    assert client.completions.calls[0] == (
        "ok-model",
        [{"role": "user", "content": "Return exactly OK."}],
        16,
    )
    assert results[0].ok is True
    assert results[0].detail == "OK."
    assert results[1].ok is False
    assert "RuntimeError" in results[1].detail
    assert "csrf-secret" not in results[1].detail
    assert "WinError" not in results[1].detail
    assert "10061" not in results[1].detail
