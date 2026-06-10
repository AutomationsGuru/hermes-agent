"""Sanitized live probe for Antigravity Cascade."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.google_antigravity_bridge import check_antigravity_available
from agent.google_antigravity_cascade import (
    AntigravityCascadeClient,
    AntigravityCascadeError,
    AntigravityCascadeEvent,
    AntigravityCascadeSession,
    sanitize_console_text,
)


def summarize_events(events: Iterable[AntigravityCascadeEvent]) -> dict[str, object]:
    event_list = list(events)
    types: list[str] = []
    for event in event_list:
        if event.type not in types:
            types.append(event.type)
    text_prefix = ""
    for event in event_list:
        if event.type == "assistant_text" and event.text:
            text_prefix = sanitize_console_text(event.text)
            break
    return {
        "count": len(event_list),
        "assistant_text": any(event.type == "assistant_text" for event in event_list),
        "done": any(event.type == "done" for event in event_list),
        "error": any(event.type == "error" for event in event_list),
        "tool_call": any(event.type == "tool_call" for event in event_list),
        "tool_result": any(event.type == "tool_result" for event in event_list),
        "unknown": any(event.type == "unknown" for event in event_list),
        "types": types,
        "text_prefix": text_prefix,
    }


def format_available_line(
    *, available: bool, base_url: str = "", reason: str = ""
) -> str:
    if available:
        return (
            "ANTIGRAVITY_CASCADE_PROBE "
            f"available=True base_url={sanitize_console_text(base_url)}"
        )
    return (
        "ANTIGRAVITY_CASCADE_PROBE available=False "
        f"reason={sanitize_console_text(reason)}"
    )


def format_start_line(session: AntigravityCascadeSession) -> str:
    return (
        f"START http={session.http_status} "
        f"cascade_id_present={bool(session.cascade_id)}"
    )


def format_send_line(http_status: int | None) -> str:
    return f"SEND http={http_status}"


def format_stream_line(summary: dict[str, object]) -> str:
    types = ",".join(summary["types"]) if summary["types"] else "-"
    return (
        f"STREAM events={summary['count']} "
        f"assistant_text={summary['assistant_text']} "
        f"done={summary['done']} "
        f"error={summary['error']} "
        f"tool_call={summary['tool_call']} "
        f"tool_result={summary['tool_result']} "
        f"unknown={summary['unknown']} "
        f"types={types}"
    )


def format_text_prefix_line(text_prefix: object) -> str:
    return f"TEXT_PREFIX {sanitize_console_text(text_prefix)}"


def format_error_line(error: BaseException) -> str:
    code = getattr(error, "code", error.__class__.__name__)
    status = getattr(error, "status_code", None)
    return (
        f"ERROR code={sanitize_console_text(code)} "
        f"status={status} message={sanitize_console_text(error)}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-enum",
        default="MODEL_PLACEHOLDER_M132",
        help="Antigravity internal model enum to request.",
    )
    parser.add_argument(
        "--prompt",
        default="Return exactly OK.",
        help="Prompt to send through the Cascade probe.",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=100,
        help="Maximum parsed stream/update events to print in the summary.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP read timeout in seconds.",
    )
    parser.add_argument(
        "--workspace-uri",
        default=None,
        help="Optional file:// workspace URI to attach to the Cascade session.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    status = check_antigravity_available()
    if not status.available:
        print(format_available_line(available=False, reason=status.reason))
        return 1

    print(format_available_line(available=True, base_url=status.base_url))
    try:
        with AntigravityCascadeClient(timeout=args.timeout) as client:
            session = client.start_cascade(
                model_enum=args.model_enum,
                workspace_uri=args.workspace_uri,
            )
            print(format_start_line(session))
            send_status = client.send_user_message(session.cascade_id, args.prompt)
            print(format_send_line(send_status))
            events = list(
                client.stream_agent_state_updates(
                    session.cascade_id,
                    max_events=args.max_events,
                )
            )
    except AntigravityCascadeError as exc:
        print(format_error_line(exc))
        return 2
    except Exception as exc:
        print(format_error_line(exc))
        return 2

    summary = summarize_events(events)
    print(format_stream_line(summary))
    if summary["text_prefix"]:
        print(format_text_prefix_line(summary["text_prefix"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
