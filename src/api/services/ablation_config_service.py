"""Runtime-local feature switches for component ablation evals.

These switches are intentionally not global product configuration. They are
small, explicit eval inputs used to answer: "what changes when this component is
disabled for a benchmark variant?"
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AblationConfig(BaseModel):
    """A local component-disable config for one eval variant."""

    run_id: str = ""
    eval_id: str = ""
    variant_id: str = "baseline"
    disabled_components: list[str] = Field(default_factory=list)
    overrides: dict[str, Any] = Field(default_factory=dict)


def normalize_component_name(component: str) -> str:
    """Normalize component ids used by docs, tests, and future API calls."""

    return str(component or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_component_enabled(component: str, config: AblationConfig | None = None) -> bool:
    """Return whether a component is enabled under a local ablation config."""

    if config is None:
        return True
    normalized = normalize_component_name(component)
    disabled = {normalize_component_name(item) for item in config.disabled_components}
    return normalized not in disabled


def make_ablation_config(
    *,
    eval_id: str = "",
    variant_id: str = "baseline",
    disabled_components: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
    run_id: str = "",
) -> AblationConfig:
    """Build a normalized ablation config without mutating global settings."""

    return AblationConfig(
        run_id=run_id,
        eval_id=eval_id,
        variant_id=variant_id or "baseline",
        disabled_components=[
            normalize_component_name(component)
            for component in (disabled_components or [])
            if normalize_component_name(component)
        ],
        overrides=dict(overrides or {}),
    )
