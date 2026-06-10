"""Read-only discovery of the live Antigravity cross-vendor model roster.

This standalone CLI enumerates the full model roster that the Antigravity model
picker shows (Gemini + Claude + GPT) by calling a single read-only RPC on the
local Antigravity language server:

    POST /exa.language_server_pb.LanguageServerService/GetAvailableModels

It is strictly READ-ONLY discovery: it issues one GET (to discover the endpoint
+ CSRF via the shared bridge) and one empty-body POST to the listing RPC. It
NEVER starts a cascade, writes files, executes tools, or calls any mutating
method. The CSRF token, headers, and raw payloads are never printed; per-model
``displayName`` strings are routed through the cascade sanitizers before output.

When Antigravity is not running, it prints a single actionable line and exits
non-zero instead of raising a stack trace.

Usage:
    python scripts/probe_antigravity_models.py
    python scripts/probe_antigravity_models.py --agent-only
    python scripts/probe_antigravity_models.py --vendor anthropic --vendor openai
    python scripts/probe_antigravity_models.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from agent.google_antigravity_bridge import (  # noqa: E402
    AntigravityBridgeError,
    discover_antigravity_endpoint,
)
from agent.google_antigravity_cascade import (  # noqa: E402
    sanitize_console_text,
    truncate_cascade_text,
)

_GET_AVAILABLE_MODELS_PATH = (
    "/exa.language_server_pb.LanguageServerService/GetAvailableModels"
)

# Antigravity's modelProvider enum -> Hermes vendor slug.
_VENDOR_BY_PROVIDER = {
    "MODEL_PROVIDER_ANTHROPIC": "anthropic",
    "MODEL_PROVIDER_OPENAI": "openai",
    "MODEL_PROVIDER_GOOGLE": "google",
}

_ACTIONABLE_DOWN = (
    "ANTIGRAVITY_MODELS_PROBE available=False "
    "action=Start the Antigravity IDE (or set HERMES_ANTIGRAVITY_URL) and retry."
)


@dataclass(frozen=True)
class RosterModel:
    """A sanitized, read-only view of one roster entry."""

    app_slug: str
    backend_enum: str
    vendor: str
    label: str
    api_provider: str
    is_internal: bool
    agent_capable: bool
    deprecated: bool


@dataclass
class Roster:
    """Discovered roster plus the picker groupings that classify it."""

    models: list[RosterModel] = field(default_factory=list)
    default_agent_model_id: str = ""
    agent_model_ids: tuple[str, ...] = ()
    deprecated_model_ids: tuple[str, ...] = ()


def _vendor_for(model_provider: str) -> str:
    return _VENDOR_BY_PROVIDER.get(str(model_provider or ""), "unknown")


def discover_antigravity_roster(
    *,
    client: httpx.Client | None = None,
) -> Roster:
    """Fetch the live roster from the read-only GetAvailableModels RPC.

    Raises ``AntigravityBridgeError`` (already token-free/actionable) when the
    local server cannot be reached or the RPC fails.
    """

    close_client = client is None
    http = client or httpx.Client(
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        # Antigravity's local server uses a self-signed certificate on
        # 127.0.0.1; certificate verification is intentionally disabled for
        # this loopback-only connection (matches the bridge/cascade clients).
        verify=False,
    )
    try:
        endpoint = discover_antigravity_endpoint(client=http)
        response = http.post(
            endpoint.base_url + _GET_AVAILABLE_MODELS_PATH,
            json={},
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-codeium-csrf-token": endpoint.csrf_token,
            },
        )
        if response.status_code != 200:
            raise AntigravityBridgeError(
                f"GetAvailableModels HTTP {response.status_code}",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AntigravityBridgeError(
                "GetAvailableModels returned non-JSON body"
            ) from exc
    finally:
        if close_client:
            http.close()

    body = payload.get("response", payload) if isinstance(payload, dict) else {}
    raw_models = body.get("models") if isinstance(body, dict) else None
    if not isinstance(raw_models, dict):
        raise AntigravityBridgeError("GetAvailableModels response has no models map")

    agent_ids = _flatten_agent_model_ids(body.get("agentModelSorts"))
    deprecated = body.get("deprecatedModelIds")
    deprecated_ids = tuple(deprecated) if isinstance(deprecated, dict) else ()

    models: list[RosterModel] = []
    for slug, info in sorted(raw_models.items()):
        if not isinstance(info, dict):
            continue
        # ``displayName`` is cosmetic and occasionally stale/recycled in
        # Antigravity's own data; the slug + backend enum stay authoritative.
        label = truncate_cascade_text(info.get("displayName") or "", max_chars=60)
        models.append(
            RosterModel(
                app_slug=str(slug),
                backend_enum=str(info.get("model") or ""),
                vendor=_vendor_for(info.get("modelProvider")),
                label=label,
                api_provider=str(info.get("apiProvider") or ""),
                is_internal=bool(info.get("isInternal")),
                agent_capable=str(slug) in agent_ids,
                deprecated=str(slug) in deprecated_ids,
            )
        )

    return Roster(
        models=models,
        default_agent_model_id=str(body.get("defaultAgentModelId") or ""),
        agent_model_ids=tuple(agent_ids),
        deprecated_model_ids=deprecated_ids,
    )


def _flatten_agent_model_ids(agent_model_sorts: object) -> set[str]:
    """Collect the agent-capable picker slugs from agentModelSorts."""

    ids: set[str] = set()
    if not isinstance(agent_model_sorts, list):
        return ids
    for sort in agent_model_sorts:
        if not isinstance(sort, dict):
            continue
        for group in sort.get("groups") or []:
            if not isinstance(group, dict):
                continue
            for model_id in group.get("modelIds") or []:
                if isinstance(model_id, str):
                    ids.add(model_id)
    return ids


def _select(roster: Roster, *, agent_only: bool, vendors: set[str]) -> list[RosterModel]:
    out: list[RosterModel] = []
    for model in roster.models:
        if agent_only and not model.agent_capable:
            continue
        if vendors and model.vendor not in vendors:
            continue
        out.append(model)
    return out


def _format_model_line(model: RosterModel) -> str:
    flags = []
    if model.agent_capable:
        flags.append("agent")
    if model.deprecated:
        flags.append("deprecated")
    if model.is_internal:
        flags.append("internal")
    flag_text = ",".join(flags) if flags else "-"
    label = sanitize_console_text(model.label) if model.label else "-"
    return (
        f"{model.app_slug:<28} | {model.vendor:<9} | "
        f"{model.backend_enum:<32} | {flag_text:<22} | {label}"
    )


def _print_text(roster: Roster, selected: list[RosterModel]) -> None:
    print(
        "ANTIGRAVITY_MODELS_PROBE available=True source=GetAvailableModels "
        f"total={len(roster.models)} shown={len(selected)}"
    )
    print(
        "DEFAULT_AGENT_MODEL "
        f"{sanitize_console_text(roster.default_agent_model_id) or '-'}"
    )
    counts: dict[str, int] = {}
    for model in selected:
        counts[model.vendor] = counts.get(model.vendor, 0) + 1
    count_text = " ".join(
        f"{vendor}={counts[vendor]}" for vendor in sorted(counts)
    )
    print(f"VENDOR_COUNTS {count_text or '-'}")
    print("-" * 110)
    print(
        f"{'app_slug':<28} | {'vendor':<9} | "
        f"{'backend_enum':<32} | {'flags':<22} | label"
    )
    print("-" * 110)
    for model in selected:
        print(_format_model_line(model))
    cross = [m for m in selected if m.vendor in {"anthropic", "openai"}]
    if cross:
        print("-" * 110)
        print("CROSS_VENDOR (Claude/GPT the user wants):")
        for model in cross:
            print(f"  {model.app_slug} -> {model.backend_enum} ({model.vendor})")


def _print_json(roster: Roster, selected: list[RosterModel]) -> None:
    payload = {
        "available": True,
        "source": "GetAvailableModels",
        "default_agent_model_id": sanitize_console_text(
            roster.default_agent_model_id
        ),
        "total": len(roster.models),
        "models": [
            {
                "app_slug": model.app_slug,
                "backend_enum": model.backend_enum,
                "vendor": model.vendor,
                "label": sanitize_console_text(model.label),
                "api_provider": model.api_provider,
                "agent_capable": model.agent_capable,
                "deprecated": model.deprecated,
                "is_internal": model.is_internal,
            }
            for model in selected
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Antigravity model roster discovery (no secrets).",
    )
    parser.add_argument(
        "--agent-only",
        action="store_true",
        help="Only show agent-capable picker models (agentModelSorts group).",
    )
    parser.add_argument(
        "--vendor",
        action="append",
        default=[],
        choices=["anthropic", "openai", "google"],
        help="Filter by vendor (repeatable).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit sanitized JSON instead of a table.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        roster = discover_antigravity_roster()
    except AntigravityBridgeError:
        print(_ACTIONABLE_DOWN)
        return 1
    except Exception:
        # Never surface raw connection/OS strings; keep the line actionable.
        print(_ACTIONABLE_DOWN)
        return 1

    selected = _select(roster, agent_only=args.agent_only, vendors=set(args.vendor))
    if args.json:
        _print_json(roster, selected)
    else:
        _print_text(roster, selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
