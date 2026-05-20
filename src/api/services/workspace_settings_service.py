"""Workspace settings persistence, defaults, and runtime resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.infra import config as config_module

DEFAULT_SETTINGS: dict[str, Any] = {
    "model": {
        "provider": "",
        "default_model": "",
        "planner_model": "",
        "coder_model": "",
        "reviewer_model": "",
        "temperature": 0.2,
        "max_tokens": 8192,
    },
    "safety": {
        "require_approval_for_shell": True,
        "require_approval_for_file_delete": True,
        "require_approval_for_git_discard": True,
        "allow_network": False,
        "trusted_workspace": False,
    },
    "indexing": {
        "ignore": ["node_modules", ".git", "dist", "build", "__pycache__", ".venv", ".nanocursor"],
        "max_file_size_kb": 512,
        "include_tests": True,
    },
    "capabilities": {
        "enabled_skills": [],
        "disabled_skills": [],
        "enabled_mcp": [],
        "disabled_mcp": [],
    },
    "runtime": {
        "default_strategy": "auto",
        "max_concurrent_write_runs": 1,
        "auto_create_git_branch": False,
        "auto_checkpoint": True,
    },
}


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _settings_path(workspace: Path) -> Path:
    nc = workspace / ".nanocursor"
    nc.mkdir(parents=True, exist_ok=True)
    return nc / "settings.json"


def get_workspace_settings(workspace_dir: str | None = None) -> dict[str, Any]:
    """Read workspace settings, merged with defaults."""
    workspace = _workspace(workspace_dir)
    sp = _settings_path(workspace)
    saved = {}
    if sp.exists():
        try:
            saved = json.loads(sp.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                saved = {}
        except (json.JSONDecodeError, OSError):
            saved = {}

    return _deep_merge(DEFAULT_SETTINGS, saved)


def save_workspace_settings(
    settings: dict[str, Any] | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Deep-merge incoming settings with existing and persist."""
    workspace = _workspace(workspace_dir)
    sp = _settings_path(workspace)
    existing = get_workspace_settings(str(workspace))
    merged = _deep_merge(existing, settings or {})
    sp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base. Returns a new dict."""
    result = {**base}
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---- D4: Settings Runtime ----

def get_effective_settings(workspace_dir: str | None = None) -> dict[str, Any]:
    """Merge defaults → env overrides → workspace settings → runtime overrides."""
    ws_settings = get_workspace_settings(workspace_dir)

    # Env overrides (highest priority for model)
    env = os.environ
    if env.get("LLM_PROVIDER"):
        ws_settings["model"]["provider"] = env["LLM_PROVIDER"]
    if env.get("LLM_TEMPERATURE"):
        try:
            ws_settings["model"]["temperature"] = float(env["LLM_TEMPERATURE"])
        except ValueError:
            pass
    if env.get("LLM_MAX_TOKENS"):
        try:
            ws_settings["model"]["max_tokens"] = int(env["LLM_MAX_TOKENS"])
        except ValueError:
            pass

    # Runtime constraints
    ws_settings["runtime"]["max_concurrent_write_runs"] = max(
        ws_settings["runtime"].get("max_concurrent_write_runs", 1), 0
    )

    return ws_settings


def get_effective_model_settings(role: str = "", workspace_dir: str | None = None) -> dict[str, Any]:
    """Return model settings for a specific Agent role."""
    settings = get_effective_settings(workspace_dir)
    model = settings.get("model", {})
    provider = model.get("provider", "")
    temperature = model.get("temperature", 0.2)
    max_tokens = model.get("max_tokens", 8192)

    model_name = model.get("default_model", "")
    if role and model.get(f"{role}_model"):
        model_name = model[f"{role}_model"]

    return {
        "provider": provider,
        "model": model_name,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }


def is_capability_enabled(capability_id: str, workspace_dir: str | None = None) -> bool:
    """Check if a skill/MCP capability is enabled in workspace settings."""
    settings = get_effective_settings(workspace_dir)
    caps = settings.get("capabilities", {})

    if capability_id.startswith("skill."):
        disabled = caps.get("disabled_skills", [])
        if disabled and capability_id in disabled:
            return False
        enabled = caps.get("enabled_skills", [])
        if enabled and capability_id not in enabled:
            return False
        return True

    if capability_id.startswith("mcp."):
        disabled = caps.get("disabled_mcp", [])
        if disabled and capability_id in disabled:
            return False
        enabled = caps.get("enabled_mcp", [])
        if enabled and capability_id not in enabled:
            return False
        return True

    return True


def validate_settings(settings: dict[str, Any] | None = None, workspace_dir: str | None = None) -> dict[str, Any]:
    """Validate workspace settings and return checks."""
    s = settings or get_workspace_settings(workspace_dir)
    checks: list[dict[str, Any]] = []

    # Model provider check
    provider = s.get("model", {}).get("provider", "")
    if provider:
        key_map = {
            "deepseek": "DEEPSEEK_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "minimax": "MINIMAX_API_KEY",
        }
        expected_key = key_map.get(provider)
        if expected_key and not os.environ.get(expected_key):
            checks.append({
                "id": "model.provider",
                "status": "warning",
                "message": f"已设置 provider={provider} 但环境变量 {expected_key} 未配置。",
            })
        elif expected_key:
            checks.append({
                "id": "model.provider",
                "status": "passed",
                "message": f"provider={provider}，{expected_key} 已配置。",
            })
    else:
        # Auto-detect
        found = False
        for p, key in [("deepseek", "DEEPSEEK_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"),
                        ("openai", "OPENAI_API_KEY"), ("minimax", "MINIMAX_API_KEY")]:
            if os.environ.get(key):
                found = True
                break
        if not found and not os.environ.get("OLLAMA_BASE_URL"):
            checks.append({
                "id": "model.provider",
                "status": "warning",
                "message": "未检测到任何 LLM API key，请配置 .env 或 workspace settings。",
            })
        else:
            checks.append({
                "id": "model.provider",
                "status": "passed",
                "message": "已检测到 LLM 配置。",
            })

    # Workspace writable
    workspace = _workspace(workspace_dir)
    writable = os.access(workspace, os.W_OK) if workspace.exists() else False
    checks.append({
        "id": "workspace.writable",
        "status": "passed" if writable else "warning",
        "message": "工作区可写。" if writable else "工作区不可写，某些功能可能无法正常工作。",
    })

    # Git check
    has_git = (workspace / ".git").exists()
    runtime = s.get("runtime", {})
    if runtime.get("auto_create_git_branch") and not has_git:
        checks.append({
            "id": "runtime.git",
            "status": "warning",
            "message": "auto_create_git_branch 已启用但当前工作区不是 Git 仓库。",
        })

    ok = not any(c["status"] == "warning" for c in checks)

    return {
        "ok": ok,
        "checks": checks,
    }
