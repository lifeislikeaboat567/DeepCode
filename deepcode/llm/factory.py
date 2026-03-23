"""Factory function for creating LLM client instances."""

from __future__ import annotations

from deepcode.config import Settings, get_settings
from deepcode.exceptions import ConfigurationError
from deepcode.llm.base import BaseLLMClient


def create_llm_client(settings: Settings | None = None) -> BaseLLMClient:
    """Create and return an :class:`BaseLLMClient` based on configuration.

    Args:
        settings: Optional settings instance; falls back to the global singleton.

    Returns:
        A concrete :class:`BaseLLMClient` implementation.

    Raises:
        ConfigurationError: If the configured provider is not supported.
    """
    cfg = settings or get_settings()
    provider = cfg.llm_provider

    # Backward-compatible aliases for OpenAI-compatible providers.
    if provider == "github":
        provider = "github_copilot"

    if provider == "mock":
        from deepcode.llm.mock_client import MockLLMClient

        return MockLLMClient()

    if provider == "openai":
        from deepcode.llm.openai_client import OpenAIClient

        return OpenAIClient(
            api_key=cfg.llm_api_key,
            model=cfg.llm_model,
            base_url=cfg.llm_base_url,
            temperature=cfg.llm_temperature,
            max_tokens=cfg.llm_max_tokens,
            enable_thinking=cfg.llm_enable_thinking,
        )

    if provider == "ollama":
        from deepcode.llm.openai_client import OpenAIClient

        # Ollama exposes an OpenAI-compatible API
        base_url = cfg.llm_base_url or "http://localhost:11434/v1"
        return OpenAIClient(
            api_key=cfg.llm_api_key or "ollama",
            model=cfg.llm_model,
            base_url=base_url,
            temperature=cfg.llm_temperature,
            max_tokens=cfg.llm_max_tokens,
            enable_thinking=cfg.llm_enable_thinking,
        )

    if provider == "gemini":
        from deepcode.llm.openai_client import OpenAIClient

        # Gemini provides an OpenAI-compatible endpoint.
        base_url = cfg.llm_base_url or "https://generativelanguage.googleapis.com/v1beta/openai/"
        model = cfg.llm_model or "gemini-2.0-flash"
        return OpenAIClient(
            api_key=cfg.llm_api_key,
            model=model,
            base_url=base_url,
            temperature=cfg.llm_temperature,
            max_tokens=cfg.llm_max_tokens,
            enable_thinking=cfg.llm_enable_thinking,
        )

    if provider == "github_copilot":
        from deepcode.llm.openai_client import OpenAIClient

        # GitHub Models exposes an OpenAI-compatible endpoint.
        base_url = cfg.llm_base_url or "https://models.inference.ai.azure.com"
        model = cfg.llm_model or "gpt-4o-mini"
        return OpenAIClient(
            api_key=cfg.llm_api_key,
            model=model,
            base_url=base_url,
            temperature=cfg.llm_temperature,
            max_tokens=cfg.llm_max_tokens,
            enable_thinking=cfg.llm_enable_thinking,
        )

    raise ConfigurationError(
        f"Unsupported LLM provider '{provider}'. "
        "Supported values: openai, ollama, gemini, github_copilot, mock"
    )
