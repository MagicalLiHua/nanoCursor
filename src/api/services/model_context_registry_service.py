"""Model context window registry and user overrides.

The registry gives the runtime a conservative context-window contract for the
current model. Builtins are intentionally small and explicit; users can override
unknown or newly released models without changing code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ModelContextSpec(BaseModel):
    provider: str
    model: str
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    watch_ratio: float = Field(default=0.60, gt=0, lt=1)
    soft_compact_ratio: float = Field(default=0.75, gt=0, lt=1)
    hard_compact_ratio: float = Field(default=0.85, gt=0, lt=1)
    emergency_ratio: float = Field(default=0.90, gt=0, lt=1)
    source: str = "builtin"
    last_verified: str | None = None
    notes: str = ""

    @field_validator("provider", "model")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    def normalized_key(self) -> tuple[str, str]:
        return normalize_provider(self.provider), normalize_model(self.model)

    def thresholds(self) -> dict[str, float]:
        return {
            "watch": self.watch_ratio,
            "soft_compact": self.soft_compact_ratio,
            "hard_compact": self.hard_compact_ratio,
            "emergency": self.emergency_ratio,
        }


def normalize_provider(provider: str | None) -> str:
    return str(provider or "unknown").strip().lower()


def normalize_model(model: str | None) -> str:
    return str(model or "unknown").strip().lower()


def _spec(
    provider: str,
    model: str,
    context_window: int,
    max_output_tokens: int,
    *,
    last_verified: str = "2026-06-11",
    notes: str = "",
) -> ModelContextSpec:
    return ModelContextSpec(
        provider=provider,
        model=model,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        last_verified=last_verified,
        notes=notes,
    )


BUILTIN_MODEL_CONTEXTS: dict[tuple[str, str], ModelContextSpec] = {
    ("openai", "gpt-5.5"): _spec("openai", "gpt-5.5", 1_000_000, 128_000),
    ("openai", "gpt-5.4"): _spec("openai", "gpt-5.4", 1_000_000, 128_000),
    ("openai", "gpt-5.4-mini"): _spec("openai", "gpt-5.4-mini", 400_000, 128_000),
    ("openai", "gpt-4.1"): _spec("openai", "gpt-4.1", 1_000_000, 32_768),
    ("openai", "gpt-4o"): _spec("openai", "gpt-4o", 128_000, 16_384),
    ("anthropic", "claude-sonnet-4-6"): _spec("anthropic", "claude-sonnet-4-6", 1_000_000, 128_000),
    ("anthropic", "claude-sonnet-4.6"): _spec("anthropic", "claude-sonnet-4.6", 1_000_000, 128_000),
    ("anthropic", "claude-sonnet-4-5"): _spec("anthropic", "claude-sonnet-4-5", 1_000_000, 128_000),
    ("anthropic", "claude-sonnet-4.5"): _spec("anthropic", "claude-sonnet-4.5", 1_000_000, 128_000),
    ("deepseek", "deepseek-v4-flash"): _spec("deepseek", "deepseek-v4-flash", 1_000_000, 384_000),
    ("deepseek", "deepseek-v4-pro"): _spec("deepseek", "deepseek-v4-pro", 1_000_000, 384_000),
    ("deepseek", "deepseek-chat"): _spec("deepseek", "deepseek-chat", 1_000_000, 384_000),
    ("deepseek", "deepseek-reasoner"): _spec("deepseek", "deepseek-reasoner", 1_000_000, 384_000),
    ("qwen", "qwen3-max"): _spec("qwen", "qwen3-max", 262_144, 32_768),
    ("qwen", "qwen-long-latest"): _spec("qwen", "qwen-long-latest", 10_000_000, 32_768),
    ("ollama", "qwen2.5-coder"): _spec("ollama", "qwen2.5-coder", 128_000, 8_192),
    ("minimax", "minimax-m2.7"): _spec("minimax", "MiniMax-M2.7", 128_000, 16_000),
}


DEFAULT_CONTEXT_SPEC = ModelContextSpec(
    provider="unknown",
    model="unknown",
    context_window=128_000,
    max_output_tokens=16_000,
    source="fallback",
    last_verified=None,
    notes="Conservative fallback for unknown models.",
)


def model_override_path(workspace_dir: str | Path | None) -> Path:
    workspace = Path(workspace_dir) if workspace_dir else _current_workspace_path()
    return workspace / ".nanocursor" / "context" / "model_overrides.json"


def load_model_context_overrides(workspace_dir: str | Path | None = None) -> dict[tuple[str, str], ModelContextSpec]:
    path = model_override_path(workspace_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = raw.get("models", raw) if isinstance(raw, dict) else raw
    overrides: dict[tuple[str, str], ModelContextSpec] = {}
    if not isinstance(items, list):
        return overrides
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            payload = {**item, "source": item.get("source") or "override"}
            spec = ModelContextSpec(**payload)
        except Exception:
            continue
        spec.source = "override"
        overrides[spec.normalized_key()] = spec
    return overrides


def save_model_context_override(spec: ModelContextSpec, workspace_dir: str | Path | None = None) -> ModelContextSpec:
    override = spec.model_copy(update={"source": "override"})
    overrides = load_model_context_overrides(workspace_dir)
    overrides[override.normalized_key()] = override
    path = model_override_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "models": [
            item.model_dump()
            for item in sorted(overrides.values(), key=lambda value: value.normalized_key())
        ]
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return override


def list_model_context_specs(workspace_dir: str | Path | None = None) -> dict[str, list[dict[str, Any]]]:
    overrides = load_model_context_overrides(workspace_dir)
    builtin_keys = set(BUILTIN_MODEL_CONTEXTS)
    return {
        "models": [
            spec.model_dump()
            for spec in sorted(BUILTIN_MODEL_CONTEXTS.values(), key=lambda value: value.normalized_key())
        ],
        "overrides": [
            spec.model_dump()
            for spec in sorted(overrides.values(), key=lambda value: value.normalized_key())
        ],
        "override_count": len(overrides),
        "builtin_count": len(builtin_keys),
    }


def get_model_context_spec(
    provider: str | None,
    model: str | None,
    workspace_dir: str | Path | None = None,
) -> ModelContextSpec:
    provider_key = normalize_provider(provider)
    model_key = normalize_model(model)
    overrides = load_model_context_overrides(workspace_dir)
    if (provider_key, model_key) in overrides:
        return overrides[(provider_key, model_key)]
    if (provider_key, model_key) in BUILTIN_MODEL_CONTEXTS:
        return BUILTIN_MODEL_CONTEXTS[(provider_key, model_key)]
    fallback = DEFAULT_CONTEXT_SPEC.model_copy(
        update={"provider": provider_key or "unknown", "model": model_key or "unknown"}
    )
    return fallback


def get_current_model_context_spec(workspace_dir: str | Path | None = None) -> ModelContextSpec:
    from src.infra.llm_config import get_runtime_llm_config

    model, _api_key, _base_url, provider = get_runtime_llm_config()
    return get_model_context_spec(provider, model, workspace_dir)


def _current_workspace_path() -> Path:
    from src.infra import config as config_module

    return Path(config_module.WORKSPACE_DIR)
