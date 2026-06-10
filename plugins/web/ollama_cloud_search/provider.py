"""Ollama Cloud web search provider.

Search-only plugin for Ollama's cloud-hosted web-search endpoint. It follows
Hermes' standard web provider contract and maps Ollama search results into the
legacy ``{success, data: {web: [...]}}`` shape consumed by ``web_search``.

Config keys this provider responds to::

    web:
      search_backend: "ollama-cloud-search"
      backend: "ollama-cloud-search"  # search only; extract falls back

Env vars::

    OLLAMA_API_KEY=...              # required, from https://ollama.com/settings
    OLLAMA_WEB_SEARCH_URL=...       # optional full endpoint override
    OLLAMA_BASE_URL=...             # optional; /v1 suffix is stripped for search
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)


def _get_env_value(key: str) -> str:
    """Read env through Hermes' config-aware helper, falling back to os.environ."""
    try:
        from hermes_cli.config import get_env_value

        value = get_env_value(key)
    except Exception:
        value = None
    if value is None:
        import os

        value = os.getenv(key, "")
    return (value or "").strip()


def _ollama_api_key() -> str:
    """Return the configured Ollama Cloud API key, if any."""
    return _get_env_value("OLLAMA_API_KEY")


def _ollama_web_search_url() -> str:
    """Return the Ollama Cloud web-search endpoint.

    ``OLLAMA_BASE_URL`` is usually the OpenAI-compatible chat base URL
    (``https://ollama.com/v1``). The web-search endpoint lives one level up at
    ``https://ollama.com/api/web_search``, so strip a trailing ``/v1`` before
    appending the API path.
    """
    explicit = _get_env_value("OLLAMA_WEB_SEARCH_URL")
    if explicit:
        return explicit

    base_url = _get_env_value("OLLAMA_BASE_URL") or "https://ollama.com"
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    return urljoin(f"{base_url}/", "api/web_search")


def _normalize_ollama_cloud_search_results(response: dict[str, Any]) -> dict[str, Any]:
    """Map Ollama ``/api/web_search`` JSON into Hermes' standard shape."""
    web_results: list[dict[str, Any]] = []
    for i, result in enumerate(response.get("results", []) or []):
        if not isinstance(result, dict):
            continue
        web_results.append(
            {
                "title": str(result.get("title") or ""),
                "url": str(result.get("url") or ""),
                "description": str(
                    result.get("content")
                    or result.get("description")
                    or result.get("snippet")
                    or ""
                ),
                "position": i + 1,
            }
        )
    return {"success": True, "data": {"web": web_results}}


class OllamaCloudSearchProvider(WebSearchProvider):
    """Search-only provider backed by Ollama Cloud Search."""

    @property
    def name(self) -> str:
        return "ollama-cloud-search"

    @property
    def display_name(self) -> str:
        return "Ollama Cloud Search"

    def is_available(self) -> bool:
        """Return True when ``OLLAMA_API_KEY`` is set to a non-empty value."""
        return bool(_ollama_api_key())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Execute an Ollama Cloud web search."""
        api_key = _ollama_api_key()
        if not api_key:
            return {
                "success": False,
                "error": (
                    "OLLAMA_API_KEY environment variable not set. "
                    "Get your API key at https://ollama.com/settings"
                ),
            }

        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return {"success": False, "error": "Interrupted"}

            import httpx

            url = _ollama_web_search_url()
            logger.info("Ollama Cloud search: %r (limit=%d)", query, limit)
            response = httpx.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "max_results": max(1, min(limit, 20))},
                timeout=60,
            )
            response.raise_for_status()
            return _normalize_ollama_cloud_search_results(response.json())
        except Exception as exc:  # noqa: BLE001 — includes HTTP/client errors
            logger.warning("Ollama Cloud search error: %s", exc)
            return {"success": False, "error": f"Ollama Cloud search failed: {exc}"}

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": "Ollama Cloud Search",
            "badge": "owned API · search-only",
            "tag": "Uses Ollama Cloud /api/web_search. Pair with a separate extract backend.",
            "env_vars": [
                {
                    "key": "OLLAMA_API_KEY",
                    "prompt": "Ollama Cloud API key",
                    "url": "https://ollama.com/settings",
                },
                {
                    "key": "OLLAMA_WEB_SEARCH_URL",
                    "prompt": "Optional full Ollama Cloud web-search endpoint override",
                    "url": None,
                    "optional": True,
                },
            ],
        }
