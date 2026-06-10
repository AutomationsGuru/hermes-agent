"""Local Antigravity language-server bridge.

Antigravity exposes some app-visible model choices that do not route through
Cloud Code Assist's direct ``v1internal:generateContent`` model slug path.  The
local Antigravity language server can still run those choices through
``GetModelResponse`` when called with its internal model enum.

This module is intentionally a small text-only bridge.  It discovers the active
local Antigravity server, extracts the CSRF token from the app page, and calls:

    /exa.language_server_pb.LanguageServerService/GetModelResponse

Descriptor and live probes show ``GetModelResponseRequest`` only accepts
``prompt`` and ``model`` and returns a plain ``response`` string.  Antigravity
also exposes richer Cascade/chat methods, but those are stateful agent surfaces
rather than a synchronous chat-completions/tool-call API.  Keep this bridge
text-only until a safe Cascade adapter is designed and verified separately.

No Google OAuth bearer token is read or printed by this bridge.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Iterator, Optional

import httpx


class AntigravityBridgeError(RuntimeError):
    """Raised when the local Antigravity bridge is unavailable or fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = "antigravity_bridge_error"


@dataclass(frozen=True)
class AntigravityEndpoint:
    base_url: str
    csrf_token: str


@dataclass(frozen=True)
class AntigravityBridgeStatus:
    """Safe, token-free availability result for UI/status surfaces."""

    available: bool
    base_url: str = ""
    reason: str = ""


_ACTIONABLE_UNAVAILABLE = (
    "Antigravity bridge unavailable. Start Antigravity or set HERMES_ANTIGRAVITY_URL."
)

_APP_CONFIG_CSRF_RE = re.compile(r'"csrfToken"\s*:\s*"([^"]+)"')
_LOCAL_URL_RE = re.compile(r"https?://127\.0\.0\.1:\d+/?")


def _default_main_log_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Antigravity" / "logs" / "main.log"
    return Path.home() / "AppData" / "Roaming" / "Antigravity" / "logs" / "main.log"


def _normalize_base_url(url: str) -> str:
    value = str(url or "").strip()
    return value.rstrip("/")


def _candidate_base_urls(log_path: Path | None = None) -> list[str]:
    """Return newest-first local Antigravity base URL candidates."""

    candidates: list[str] = []
    for env_name in ("HERMES_ANTIGRAVITY_URL", "ANTIGRAVITY_URL"):
        value = _normalize_base_url(os.environ.get(env_name, ""))
        if value:
            candidates.append(value)

    path = log_path or _default_main_log_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = ""
    if text:
        # main.log appends over time; prefer the last URL so stale ports do not
        # win after Antigravity restarts.
        for match in reversed(_LOCAL_URL_RE.findall(text)):
            candidates.append(_normalize_base_url(match))

    seen: set[str] = set()
    unique: list[str] = []
    for url in candidates:
        if not url:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(url)
    return unique


def _extract_csrf_token(html: str) -> str:
    match = _APP_CONFIG_CSRF_RE.search(html or "")
    return match.group(1) if match else ""


def discover_antigravity_endpoint(
    *,
    log_path: Path | None = None,
    client: httpx.Client | None = None,
) -> AntigravityEndpoint:
    """Find a live local Antigravity server and return its CSRF token."""

    urls = _candidate_base_urls(log_path=log_path)
    if not urls:
        raise AntigravityBridgeError(_ACTIONABLE_UNAVAILABLE)

    close_client = client is None
    http = client or httpx.Client(
        timeout=httpx.Timeout(connect=3.0, read=10.0, write=5.0, pool=5.0), verify=False
    )
    try:
        for base_url in urls:
            try:
                response = http.get(base_url)
                if response.status_code != 200:
                    continue
                csrf = _extract_csrf_token(response.text)
                if not csrf:
                    continue
                return AntigravityEndpoint(base_url=base_url, csrf_token=csrf)
            except Exception:
                continue
    finally:
        if close_client:
            http.close()

    # The detailed connection failures may contain local OS/network strings and
    # do not help end users. Keep them out of model responses/log-fed context;
    # status helpers can answer the only actionable question: start the app or
    # provide the explicit URL.
    raise AntigravityBridgeError(_ACTIONABLE_UNAVAILABLE)


def check_antigravity_available(
    *,
    log_path: Path | None = None,
    client: httpx.Client | None = None,
) -> AntigravityBridgeStatus:
    """Return token-free availability status for the local Antigravity bridge."""

    try:
        endpoint = discover_antigravity_endpoint(log_path=log_path, client=client)
    except Exception:
        return AntigravityBridgeStatus(
            available=False,
            reason=_ACTIONABLE_UNAVAILABLE,
        )
    return AntigravityBridgeStatus(available=True, base_url=endpoint.base_url)


def build_antigravity_prompt(messages: Iterable[dict[str, Any]] | None) -> str:
    """Flatten OpenAI-style messages into Antigravity's prompt string."""

    system_parts: list[str] = []
    transcript: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().lower()
        content = _coerce_content_to_text(message.get("content"))
        if not content and role not in {"assistant", "tool", "function"}:
            continue
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        if role == "assistant":
            label = "Assistant"
        elif role in {"tool", "function"}:
            label = "Tool"
        else:
            label = "User"
        if content:
            transcript.append(f"{label}: {content}")

    prompt_parts: list[str] = []
    if system_parts:
        prompt_parts.append("System:\n" + "\n".join(system_parts))
    if transcript:
        prompt_parts.append("Conversation:\n" + "\n".join(transcript))
    return "\n\n".join(prompt_parts).strip()


def _coerce_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    pieces.append(part["text"])
                elif part.get("type") == "text" and isinstance(part.get("text"), str):
                    pieces.append(part["text"])
        return "\n".join(piece for piece in pieces if piece)
    return str(content)


def get_model_response(
    *,
    model_enum: str,
    prompt: str,
    endpoint: AntigravityEndpoint | None = None,
    client: httpx.Client | None = None,
    log_path: Path | None = None,
) -> str:
    """Call Antigravity's text-only GetModelResponse endpoint."""

    enum = str(model_enum or "").strip()
    if not enum:
        raise AntigravityBridgeError("Antigravity model enum is required")
    if not str(prompt or "").strip():
        raise AntigravityBridgeError("Antigravity prompt is empty")

    close_client = client is None
    http = client or httpx.Client(
        timeout=httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=10.0),
        verify=False,
    )
    try:
        ep = endpoint or discover_antigravity_endpoint(log_path=log_path, client=http)
        url = f"{ep.base_url}/exa.language_server_pb.LanguageServerService/GetModelResponse"
        response = http.post(
            url,
            json={"prompt": prompt, "model": enum},
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-codeium-csrf-token": ep.csrf_token,
            },
        )
        if response.status_code != 200:
            body = response.text[:500]
            raise AntigravityBridgeError(
                f"Antigravity GetModelResponse HTTP {response.status_code}: {body}",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AntigravityBridgeError(
                f"Antigravity returned invalid JSON: {exc}"
            ) from exc
        text = payload.get("response") if isinstance(payload, dict) else ""
        if isinstance(text, str):
            return text
        return str(text or "")
    finally:
        if close_client:
            http.close()


def translate_antigravity_text_response(text: str, model: str) -> SimpleNamespace:
    """Return an OpenAI-chat-shaped response for Antigravity text output."""

    content = str(text or "")
    usage = SimpleNamespace(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
    )
    message = SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=None,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
    )
    choice = SimpleNamespace(index=0, message=message, finish_reason="stop")
    return SimpleNamespace(
        id=f"chatcmpl-antigravity-{uuid.uuid4().hex[:12]}",
        object="chat.completion",
        created=int(time.time()),
        model=model,
        choices=[choice],
        usage=usage,
    )


def stream_antigravity_text_response(
    text: str, model: str
) -> Iterator[SimpleNamespace]:
    """Yield OpenAI-chat-shaped streaming chunks for text-only responses."""

    delta = SimpleNamespace(
        role="assistant",
        content=str(text or ""),
        tool_calls=None,
        reasoning=None,
        reasoning_content=None,
    )
    yield SimpleNamespace(
        id=f"chatcmpl-antigravity-{uuid.uuid4().hex[:12]}",
        object="chat.completion.chunk",
        created=int(time.time()),
        model=model,
        choices=[SimpleNamespace(index=0, delta=delta, finish_reason=None)],
        usage=None,
    )
    final_delta = SimpleNamespace(
        role="assistant",
        content=None,
        tool_calls=None,
        reasoning=None,
        reasoning_content=None,
    )
    yield SimpleNamespace(
        id=f"chatcmpl-antigravity-{uuid.uuid4().hex[:12]}",
        object="chat.completion.chunk",
        created=int(time.time()),
        model=model,
        choices=[SimpleNamespace(index=0, delta=final_delta, finish_reason="stop")],
        usage=None,
    )
