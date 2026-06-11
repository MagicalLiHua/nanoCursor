from __future__ import annotations

from src.api.services.model_context_registry_service import (
    ModelContextSpec,
    get_model_context_spec,
    list_model_context_specs,
    save_model_context_override,
)


def test_model_context_registry_returns_builtin_model(tmp_path):
    spec = get_model_context_spec("openai", "gpt-5.4-mini", tmp_path)

    assert spec.provider == "openai"
    assert spec.model == "gpt-5.4-mini"
    assert spec.context_window == 400_000
    assert spec.max_output_tokens == 128_000
    assert spec.source == "builtin"


def test_model_context_registry_falls_back_for_unknown_model(tmp_path):
    spec = get_model_context_spec("custom", "tiny-local-model", tmp_path)

    assert spec.provider == "custom"
    assert spec.model == "tiny-local-model"
    assert spec.context_window == 128_000
    assert spec.source == "fallback"


def test_model_context_registry_override_wins_over_builtin(tmp_path):
    save_model_context_override(
        ModelContextSpec(
            provider="openai",
            model="gpt-4o",
            context_window=64_000,
            max_output_tokens=4_000,
        ),
        tmp_path,
    )

    spec = get_model_context_spec("OpenAI", "GPT-4O", tmp_path)

    assert spec.context_window == 64_000
    assert spec.max_output_tokens == 4_000
    assert spec.source == "override"
    assert list_model_context_specs(tmp_path)["override_count"] == 1
