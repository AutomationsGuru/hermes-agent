"""Ollama Cloud Search plugin — bundled, auto-loaded."""

from __future__ import annotations

from plugins.web.ollama_cloud_search.provider import OllamaCloudSearchProvider


def register(ctx) -> None:
    """Register the Ollama Cloud Search provider with the plugin context."""
    ctx.register_web_search_provider(OllamaCloudSearchProvider())
