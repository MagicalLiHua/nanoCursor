"""D7 Skill manifest tests."""

import pytest

from src.api.services.skill_manifest_service import (
    list_skill_versions, parse_skill_manifest, restore_skill_version,
    save_skill_version, validate_skill_content,
)
from src.api.services.capability_service import import_workspace_skill


def test_parse_skill_manifest_with_frontmatter():
    content = """---
name: API Review
version: 0.1.0
agents: [reviewer, tester]
---

# API Review

Check API endpoints."""
    manifest = parse_skill_manifest(content)
    assert manifest["name"] == "API Review"
    assert manifest["version"] == "0.1.0"
    assert manifest["agents"] == ["reviewer", "tester"]
    assert "API Review" in manifest["body"]


def test_parse_skill_manifest_multiline_list():
    content = """---
name: API Review
agents:
  - reviewer
  - tester
---

# API Review
"""
    manifest = parse_skill_manifest(content)
    assert manifest["agents"] == ["reviewer", "tester"]


def test_parse_skill_manifest_no_frontmatter():
    content = "# API Review\n\nCheck API endpoints."
    manifest = parse_skill_manifest(content)
    assert manifest["raw_content"] == content
    assert "body" not in manifest


def test_skill_id_path_escape_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError):
        list_skill_versions("../escape", str(workspace))


def test_validate_skill_content_passes():
    result = validate_skill_content("# My Skill\n\nDescription.")
    assert result["ok"] is True
    assert any(c["id"] == "not_empty" for c in result["checks"])


def test_validate_skill_content_empty():
    result = validate_skill_content("")
    assert result["ok"] is False


def test_save_and_list_skill_versions(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = import_workspace_skill("version-test", "test", "# v1", str(workspace))
    skill_id = skill["id"]

    save = save_skill_version(skill_id, "# v1", str(workspace))
    assert save["ok"] is True

    versions = list_skill_versions(skill_id, str(workspace))
    assert versions["count"] >= 1


def test_restore_skill_version(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = import_workspace_skill("restore-test", "test", "# Original", str(workspace))
    skill_id = skill["id"]

    save = save_skill_version(skill_id, "# Original", str(workspace))
    # Modify the skill
    from src.api.services.skill_service import update_workspace_skill
    update_workspace_skill(skill_id, "# Modified", str(workspace))

    result = restore_skill_version(skill_id, save["version"], str(workspace))
    assert result["ok"] is True

    # Verify restored
    from src.api.services.skill_service import get_skill_detail
    detail = get_skill_detail(skill_id, str(workspace))
    assert "Original" in detail["content"]
