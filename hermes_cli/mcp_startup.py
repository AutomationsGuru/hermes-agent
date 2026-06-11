"""Shared CLI/TUI-safe helpers for background MCP discovery."""

from __future__ import annotations

import os
import threading
from typing import Optional

_DEFAULT_DISCOVERY_WAIT_TIMEOUT = 0.75
_DISCOVERY_WAIT_ENV = "HERMES_MCP_DISCOVERY_WAIT_TIMEOUT"

_mcp_discovery_lock = threading.Lock()
_mcp_discovery_started = False
_mcp_discovery_thread: Optional[threading.Thread] = None


def get_mcp_discovery_wait_timeout(default: float = _DEFAULT_DISCOVERY_WAIT_TIMEOUT) -> float:
    """Return the bounded startup wait for background MCP discovery.

    Discovery itself still happens in a daemon thread so a dead MCP server cannot
    freeze the terminal.  This value only controls how long the CLI/TUI waits
    before the first tool snapshot/banner so slow-but-healthy servers have time
    to register instead of being shown as failed.
    """
    raw = os.getenv(_DISCOVERY_WAIT_ENV, "").strip()
    if not raw:
        try:
            from hermes_cli.config import read_raw_config

            cfg = read_raw_config() or {}
            mcp_cfg = cfg.get("mcp") if isinstance(cfg, dict) else None
            if isinstance(mcp_cfg, dict):
                raw = str(mcp_cfg.get("startup_discovery_wait_timeout", "")).strip()
        except Exception:
            raw = ""

    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return default


def _has_configured_mcp_servers() -> bool:
    """Cheap config probe so non-MCP users avoid importing the MCP stack."""
    try:
        from hermes_cli.config import read_raw_config

        mcp_servers = (read_raw_config() or {}).get("mcp_servers")
        return isinstance(mcp_servers, dict) and len(mcp_servers) > 0
    except Exception:
        # Be conservative: if config probing fails, try discovery in the
        # background so startup still can't block.
        return True


def start_background_mcp_discovery(*, logger, thread_name: str) -> None:
    """Spawn one shared background MCP discovery thread for this process."""
    global _mcp_discovery_started, _mcp_discovery_thread

    with _mcp_discovery_lock:
        if _mcp_discovery_started:
            return
        _mcp_discovery_started = True
        if not _has_configured_mcp_servers():
            return

        def _discover() -> None:
            try:
                from tools.mcp_tool import discover_mcp_tools

                discover_mcp_tools()
            except Exception:
                logger.debug("Background MCP tool discovery failed", exc_info=True)

        thread = threading.Thread(
            target=_discover,
            name=thread_name,
            daemon=True,
        )
        _mcp_discovery_thread = thread
        thread.start()


def wait_for_mcp_discovery(timeout: Optional[float] = None) -> None:
    """Briefly wait for background MCP discovery before the first tool snapshot."""
    thread = _mcp_discovery_thread
    if thread is None or not thread.is_alive():
        return
    if timeout is None:
        timeout = get_mcp_discovery_wait_timeout()
    thread.join(timeout=timeout)
