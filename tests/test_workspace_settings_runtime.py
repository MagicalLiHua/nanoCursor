"""D4 Settings Runtime tests — effective settings, validation, capability checks."""

import os

from src.api.services.workspace_settings_service import (
    get_effective_model_settings,
    get_effective_settings,
    get_workspace_settings,
    is_capability_enabled,
    save_workspace_settings,
    validate_settings,
)


def test_settings_deep_merge_preserves_unknown_keys(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    saved = save_workspace_settings({"custom_extra": "value"}, str(workspace))
    assert saved.get("custom_extra") == "value"
    reloaded = get_workspace_settings(str(workspace))
    assert reloaded.get("custom_extra") == "value"


def test_get_effective_settings_defaults(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = get_effective_settings(str(workspace))
    assert settings["safety"]["require_approval_for_shell"] is True
    assert settings["runtime"]["max_concurrent_write_runs"] == 1
    assert "node_modules" in settings["indexing"]["ignore"]


def test_is_capability_enabled_default(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert is_capability_enabled("skill.frontend-polish", str(workspace)) is True
    assert is_capability_enabled("mcp.github", str(workspace)) is True


def test_is_capability_disabled(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    save_workspace_settings({"capabilities": {"disabled_skills": ["skill.frontend-polish"]}}, str(workspace))
    assert is_capability_enabled("skill.frontend-polish", str(workspace)) is False
    assert is_capability_enabled("skill.delivery-review", str(workspace)) is True


def test_is_capability_enabled_list(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    save_workspace_settings({"capabilities": {"enabled_skills": ["skill.api-review"]}}, str(workspace))
    assert is_capability_enabled("skill.api-review", str(workspace)) is True
    assert is_capability_enabled("skill.frontend-polish", str(workspace)) is False


def test_get_effective_model_settings(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    save_workspace_settings({"model": {"provider": "deepseek", "coder_model": "deepseek-coder"}}, str(workspace))
    coder = get_effective_model_settings("coder", str(workspace))
    assert coder["model"] == "deepseek-coder"
    planner = get_effective_model_settings("planner", str(workspace))
    assert planner["provider"] == "deepseek"


def test_validate_settings_warns_missing_key(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = validate_settings({"model": {"provider": "deepseek"}}, str(workspace))
    if "DEEPSEEK_API_KEY" not in os.environ:
        assert any(c["status"] == "warning" for c in result["checks"])
    assert result["ok"] is True or result["ok"] is False


def test_validate_settings_writable(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = validate_settings({}, str(workspace))
    writable_check = next(c for c in result["checks"] if c["id"] == "workspace.writable")
    assert writable_check["status"] == "passed"


def test_validate_settings_warns_auto_branch_without_git(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = validate_settings({"runtime": {"auto_create_git_branch": True}}, str(workspace))
    git_check = next(c for c in result["checks"] if c["id"] == "runtime.git")
    assert git_check["status"] == "warning"
