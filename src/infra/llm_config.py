"""
LLM Provider Configuration - centralized provider detection.

Reads .env and auto-detects which LLM provider is configured.
Supports explicit LLM_PROVIDER override or automatic detection by
scanning for *_API_KEY env vars.

All providers use the Anthropic-compatible protocol (AsyncAnthropic SDK).
"""

import logging
import os
from typing import Optional

from dotenv import load_dotenv

# Load .env (override=False: explicit os.environ wins over .env)
load_dotenv(override=False)

logger = logging.getLogger(__name__)

# ==========================================
# Provider definitions (Anthropic-compatible endpoints)
# ==========================================

ProviderConfig = dict[str, Optional[str]]

PROVIDERS: dict[str, ProviderConfig] = {
    "anthropic": {
        "key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "base_env": "ANTHROPIC_BASE_URL",
        "default_model": "claude-sonnet-4-6",
        "default_base": None,  # SDK default
    },
    "deepseek": {
        "key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "base_env": "DEEPSEEK_BASE_URL",
        "default_model": "deepseek-chat",
        "default_base": "https://api.deepseek.com/anthropic",
    },
    "minimax": {
        "key_env": "MINIMAX_API_KEY",
        "model_env": "MINIMAX_MODEL",
        "base_env": "MINIMAX_BASE_URL",
        "default_model": "MiniMax-M2.7",
        "default_base": "https://api.minimaxi.com/anthropic",
    },
    "openai": {
        "key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "base_env": "OPENAI_BASE_URL",
        "default_model": "gpt-4o",
        "default_base": "https://api.openai.com/v1",
    },
    "ollama": {
        "key_env": "OLLAMA_BASE_URL",  # Ollama uses base_url, not key
        "model_env": "OLLAMA_MODEL",
        "base_env": "OLLAMA_BASE_URL",
        "default_model": "qwen2.5-coder",
        "default_base": "http://localhost:11434",
    },
}

# Provider detection priority (first match wins)
AUTO_DETECT_ORDER = ["anthropic", "deepseek", "minimax", "openai", "ollama"]


def _get_provider_config(provider: str) -> Optional[ProviderConfig]:
    """Get the config dict for a named provider."""
    return PROVIDERS.get(provider.lower())


def _detect_provider() -> str:
    """
    Detect which provider is configured.

    1. If LLM_PROVIDER is explicitly set, use it
    2. Otherwise scan AUTO_DETECT_ORDER for the first provider with a key set
    3. Fall back to 'minimax' as legacy default
    """
    # Explicit override
    explicit = os.getenv("LLM_PROVIDER")
    if explicit:
        explicit = explicit.lower().strip()
        if explicit in PROVIDERS:
            logger.info(f"Using explicit LLM provider: {explicit}")
            return explicit
        logger.warning(f"Unknown LLM_PROVIDER='{explicit}', falling back to auto-detect. "
                       f"Valid: {list(PROVIDERS.keys())}")

    # Auto-detect by scanning for API keys
    for provider in AUTO_DETECT_ORDER:
        cfg = PROVIDERS[provider]
        key_env = cfg["key_env"]
        has_key = bool(os.getenv(key_env))
        if has_key:
            logger.info(f"Auto-detected LLM provider: {provider} ({key_env} is set)")
            return provider

    # Nothing configured - log warning and use minimax (original default)
    logger.warning(
        "No LLM API key found. Set *_API_KEY in .env or use LLM_PROVIDER to "
        "specify a provider. Falling back to minimax."
    )
    return "minimax"


def _resolve_config(provider: str):
    """Resolve (model, api_key, base_url) for a provider."""
    cfg = PROVIDERS[provider]

    model = os.getenv(cfg["model_env"], cfg["default_model"])
    api_key = os.getenv(cfg["key_env"], "")

    # For ollama, key is the base_url being set (no actual API key needed)
    if provider == "ollama":
        api_key = "ollama"  # placeholder, ollama doesn't need a key

    base_url = os.getenv(cfg["base_env"]) or cfg["default_base"] or None

    return model, api_key, base_url


# ==========================================
# Resolved configuration (module level)
# ==========================================

_detected_provider = _detect_provider()
MODEL, API_KEY, BASE_URL = _resolve_config(_detected_provider)

# For providers that don't natively support Anthropic protocol
_WARN_PROTOCOL = {"openai", "ollama"}
if _detected_provider in _WARN_PROTOCOL:
    logger.warning(
        f"Provider '{_detected_provider}' does not natively support the Anthropic API protocol. "
        f"The system uses AsyncAnthropic SDK. Make sure your endpoint ({BASE_URL}) "
        f"offers an Anthropic-compatible API."
    )


# ==========================================
# Client factories
# ==========================================

def create_client():
    """Create an async Anthropic-compatible client for the detected provider."""
    from anthropic import AsyncAnthropic
    kwargs = {"api_key": API_KEY}
    if BASE_URL:
        kwargs["base_url"] = BASE_URL
    return AsyncAnthropic(**kwargs)


def create_sync_client():
    """Create a sync Anthropic-compatible client for the detected provider."""
    from anthropic import Anthropic
    kwargs = {"api_key": API_KEY}
    if BASE_URL:
        kwargs["base_url"] = BASE_URL
    return Anthropic(**kwargs)


def get_provider_name() -> str:
    """Return the detected provider name."""
    return _detected_provider


# ==========================================
# Export
# ==========================================

__all__ = [
    "MODEL", "API_KEY", "BASE_URL",
    "create_client", "create_sync_client",
    "get_provider_name",
]
