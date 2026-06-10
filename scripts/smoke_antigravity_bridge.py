#!/usr/bin/env python
"""Safely smoke-test approved local Antigravity bridge aliases.

This script intentionally prints only model IDs and sanitized pass/fail details.
It does not print the local CSRF token, OAuth tokens, request headers, or raw
connection-error strings.
"""

from __future__ import annotations

import sys
from typing import Iterable, NamedTuple, Sequence

from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient
from hermes_cli.model_governance import approved_picker_models_for_provider

PROMPT = "Return exactly OK."
APPROVED_ANTIGRAVITY_MODELS = list(
    approved_picker_models_for_provider("google-gemini-cli")
)


class SmokeResult(NamedTuple):
    model: str
    ok: bool
    detail: str


def _response_text(response: object) -> str:
    try:
        choices = getattr(response, "choices", []) or []
        first = choices[0]
        message = getattr(first, "message", None)
        content = getattr(message, "content", "")
    except Exception:
        content = ""
    return str(content or "").strip()


def _is_ok_text(text: str) -> bool:
    value = str(text or "").strip()
    return value == "OK" or value == "OK." or value.startswith("OK\n")


def _sanitize_error(exc: Exception) -> str:
    # Exception strings can include local OS socket details or echoed server
    # payloads. Keep output useful but intentionally not verbose.
    return f"{type(exc).__name__}: failed"


def run_smoke(
    models: Iterable[str] | None = None,
    *,
    client: object | None = None,
) -> list[SmokeResult]:
    """Run a one-prompt smoke test for each approved Antigravity alias."""

    active_client = client or GeminiCloudCodeClient()
    results: list[SmokeResult] = []
    for model in models or APPROVED_ANTIGRAVITY_MODELS:
        model_id = str(model)
        try:
            response = active_client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": PROMPT}],
                max_tokens=16,
            )
            text = _response_text(response)
            results.append(SmokeResult(model_id, _is_ok_text(text), text[:120]))
        except Exception as exc:
            results.append(SmokeResult(model_id, False, _sanitize_error(exc)))
    return results


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv  # reserved for future flags without changing the testable API
    results = run_smoke()
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"{status}\t{result.model}\t{result.detail}")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
