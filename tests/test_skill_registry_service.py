"""Skill registry, safety scan, preview, and GitHub import tests."""

from __future__ import annotations

import json
import asyncio

from fastapi.testclient import TestClient

from src.api.services.skill_github_import_service import (
    apply_github_skill_update,
    import_github_skill_async,
    check_github_skill_update,
    import_github_skill,
    preview_github_skill_import_async,
    preview_github_skill_import,
    preview_github_skill_update,
)
from src.api.services.skill_registry_service import (
    import_skill,
    list_skills,
    preview_skill_selection,
    scan_skill_content,
    set_skill_enabled,
)


def test_skill_registry_imports_skill_json_and_selection(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()

    skill = import_skill(
        "Python Dev",
        "# Python Dev\n\nUse pytest and small edits.",
        str(ws),
        skill_json={
            "id": "python-dev",
            "triggers": ["python", "pytest"],
            "agent_roles": ["coder", "tester"],
            "tool_permissions": ["read_only", "safe_write", "shell_safe"],
        },
    )

    assert skill["id"] == "skill.python-dev"
    assert skill["enabled"] is True
    assert (ws / ".nanocursor" / "skills" / "python-dev" / "skill.json").exists()

    preview = preview_skill_selection("请用 python 补 pytest", str(ws), team=[{"role": "tester"}])
    assert preview["selected"][0]["id"] == "skill.python-dev"
    assert "safe_write" in preview["selected"][0]["tool_permissions"]


def test_builtin_skill_selection_matches_chinese_ui_prompt(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()

    preview = preview_skill_selection("请优化前端按钮和聊天输入框的视觉交互，不要改后端", str(ws))

    assert any(item["id"] == "skill.frontend-polish" for item in preview["selected"])


def test_disabled_skill_is_not_selected(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    import_skill("API Review", "# API Review\n\nReview API.", str(ws), skill_json={"triggers": ["api"]})

    disabled = set_skill_enabled("skill.api-review", False, str(ws))
    assert disabled["enabled"] is False

    preview = preview_skill_selection("review api", str(ws))
    assert all(item["id"] != "skill.api-review" for item in preview["selected"])
    assert any(item["id"] == "skill.api-review" and item["reason"] == "skill disabled" for item in preview["omitted"])
    listed = list_skills(str(ws))
    assert any(skill["id"] == "skill.api-review" and skill["enabled"] is False for skill in listed["skills"])


def test_skill_preview_explains_anti_trigger_and_budget_omissions(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    import_skill(
        "Python Dev",
        "# Python Dev\n\nUse pytest.",
        str(ws),
        skill_json={"triggers": ["python"], "anti_triggers": ["frontend-only"]},
    )
    import_skill(
        "Python Test",
        "# Python Test\n\nUse pytest.",
        str(ws),
        skill_json={"triggers": ["python"]},
    )

    anti = preview_skill_selection("python frontend-only", str(ws))
    assert all(item["id"] != "skill.python-dev" for item in anti["selected"])
    assert any(item["id"] == "skill.python-dev" and "anti-trigger" in item["reason"] for item in anti["omitted"])

    budget = preview_skill_selection("python", str(ws), max_skills=1)
    assert len(budget["selected"]) == 1
    assert any(item["reason"] == "budget exceeded" for item in budget["omitted"])


def test_skill_safety_scan_blocks_high_risk_permissions():
    scan = scan_skill_content(
        "# Risky\n\nRun npm install and git push, then read API_KEY.",
        {"tool_permissions": ["read_only", "shell_risky", "git_risky"]},
    )
    assert scan["risk"] == "high"
    assert scan["default_enabled"] is False
    assert scan["allowed_permissions"] == ["read_only"]
    assert "shell_risky" in scan["blocked_permissions"]


def test_github_skill_preview_and_import_are_static(monkeypatch, tmp_path):
    from src.api.services import skill_github_import_service as svc

    files = {
        "SKILL.md": "# Python Dev\n\nUse pytest safely.",
        "skill.json": json.dumps({
            "id": "python-dev",
            "name": "Python Dev",
            "triggers": ["python"],
            "tool_permissions": ["read_only", "safe_write"],
        }),
        "examples.md": "Example",
    }

    monkeypatch.setattr(svc, "_commit_sha", lambda source, token="": "abc123")
    monkeypatch.setattr(svc, "_discover_skill_paths", lambda source, token="": ["skills/python-dev"])
    monkeypatch.setattr(svc, "_collect_skill_files", lambda source, base_path, token="": files)

    preview = preview_github_skill_import("https://github.com/example/skills", path="skills/python-dev")
    assert preview["ok"] is True
    assert preview["candidates"][0]["source"]["commit"] == "abc123"
    assert preview["candidates"][0]["risk"] == "low"

    imported = import_github_skill(
        "https://github.com/example/skills",
        path="skills/python-dev",
        workspace_dir=str(tmp_path / "workspace"),
    )
    assert imported["id"] == "skill.python-dev"
    source = json.loads(
        (tmp_path / "workspace" / ".nanocursor" / "skills" / "python-dev" / "source.json").read_text(encoding="utf-8")
    )
    assert source["type"] == "github"
    assert source["commit"] == "abc123"
    assert source["checksum"].startswith("sha256:")


def test_github_skill_async_preview_and_import_are_static(monkeypatch, tmp_path):
    from src.api.services import skill_github_import_service as svc

    files = {
        "SKILL.md": "# Async Skill\n\nUse static files only.",
        "skill.json": json.dumps({"id": "async-skill", "name": "Async Skill"}),
    }
    monkeypatch.setattr(svc, "_commit_sha", lambda source, token="": "async123")
    monkeypatch.setattr(svc, "_discover_skill_paths", lambda source, token="": ["skills/async-skill"])
    monkeypatch.setattr(svc, "_collect_skill_files", lambda source, base_path, token="": files)

    async def scenario():
        preview = await preview_github_skill_import_async(
            "https://github.com/example/skills",
            path="skills/async-skill",
        )
        imported = await import_github_skill_async(
            "https://github.com/example/skills",
            path="skills/async-skill",
            workspace_dir=str(tmp_path / "workspace"),
        )
        return preview, imported

    preview, imported = asyncio.run(scenario())

    assert preview["ok"] is True
    assert preview["candidates"][0]["id"] == "async-skill"
    assert imported["id"] == "skill.async-skill"


def test_github_skill_update_check_preview_and_apply(monkeypatch, tmp_path):
    from src.api.services import skill_github_import_service as svc

    old_files = {
        "SKILL.md": "# Python Dev\n\nUse pytest safely.",
        "skill.json": json.dumps({
            "id": "python-dev",
            "name": "Python Dev",
            "triggers": ["python"],
            "tool_permissions": ["read_only", "safe_write"],
        }),
    }
    new_files = {
        "SKILL.md": "# Python Dev\n\nUse pytest safely.\n\nRun focused tests.",
        "skill.json": json.dumps({
            "id": "python-dev",
            "name": "Python Dev",
            "triggers": ["python", "pytest"],
            "quality_rules": ["Run focused pytest after edits."],
            "tool_permissions": ["read_only", "safe_write", "shell_safe"],
        }),
    }

    state = {"commit": "abc123", "files": old_files}
    monkeypatch.setattr(svc, "_commit_sha", lambda source, token="": state["commit"])
    monkeypatch.setattr(svc, "_discover_skill_paths", lambda source, token="": ["skills/python-dev"])
    monkeypatch.setattr(svc, "_collect_skill_files", lambda source, base_path, token="": state["files"])

    workspace = tmp_path / "workspace"
    imported = import_github_skill(
        "https://github.com/example/skills",
        path="skills/python-dev",
        workspace_dir=str(workspace),
        enabled=True,
    )
    assert imported["id"] == "skill.python-dev"

    state["commit"] = "def456"
    state["files"] = new_files
    check = check_github_skill_update("skill.python-dev", workspace_dir=str(workspace))
    assert check["changed"] is True
    assert check["latest_commit"] == "def456"

    preview = preview_github_skill_update("skill.python-dev", workspace_dir=str(workspace))
    assert preview["changed"] is True
    assert preview["risk"] == "low"
    assert any(item["file"] == "SKILL.md" and "Run focused tests" in item["patch"] for item in preview["diff"])

    try:
        apply_github_skill_update("skill.python-dev", workspace_dir=str(workspace), confirmed=False)
    except ValueError as exc:
        assert "confirmed=true" in str(exc)
    else:
        raise AssertionError("expected confirmation error")

    applied = apply_github_skill_update("skill.python-dev", workspace_dir=str(workspace), confirmed=True)
    assert applied["ok"] is True
    assert applied["skill"]["source"]["commit"] == "def456"
    assert "shell_safe" in applied["skill"]["tool_permissions"]
    assert applied["skill"]["quality_rules"] == ["Run focused pytest after edits."]
    versions = list((workspace / ".nanocursor" / "skills" / "python-dev" / "versions").glob("*.md"))
    assert versions


def test_github_skill_update_rescans_risky_content(monkeypatch, tmp_path):
    from src.api.services import skill_github_import_service as svc

    old_files = {
        "SKILL.md": "# Safe Skill\n\nRead docs.",
        "skill.json": json.dumps({"id": "safe-skill", "name": "Safe Skill", "triggers": ["docs"]}),
    }
    risky_files = {
        "SKILL.md": "# Safe Skill\n\nRead API_KEY and run npm install.",
        "skill.json": json.dumps({
            "id": "safe-skill",
            "name": "Safe Skill",
            "triggers": ["docs"],
            "tool_permissions": ["read_only", "shell_risky"],
        }),
    }
    state = {"commit": "abc123", "files": old_files}
    monkeypatch.setattr(svc, "_commit_sha", lambda source, token="": state["commit"])
    monkeypatch.setattr(svc, "_discover_skill_paths", lambda source, token="": ["skills/safe-skill"])
    monkeypatch.setattr(svc, "_collect_skill_files", lambda source, base_path, token="": state["files"])

    workspace = tmp_path / "workspace"
    import_github_skill("https://github.com/example/skills", path="skills/safe-skill", workspace_dir=str(workspace))

    state["commit"] = "def456"
    state["files"] = risky_files
    preview = preview_github_skill_update("skill.safe-skill", workspace_dir=str(workspace))

    assert preview["risk"] == "high"
    assert preview["allowed_permissions"] == ["read_only"]
    assert "shell_risky" in preview["blocked_permissions"]

    applied = apply_github_skill_update("skill.safe-skill", workspace_dir=str(workspace), confirmed=True)
    assert applied["skill"]["enabled"] is False
    assert applied["skill"]["tool_permissions"] == ["read_only"]


def test_formal_skill_routes(tmp_path, monkeypatch):
    from src.api.server import app
    import src.infra.config as config_module

    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(ws))
    client = TestClient(app)

    created = client.post("/api/skills/import", json={
        "name": "API Review",
        "content": "# API Review\n\nReview API contracts.",
        "skill_json": {"triggers": ["api"], "tool_permissions": ["read_only"]},
    })
    assert created.status_code == 200
    skill_id = created.json()["skill"]["id"]

    preview = client.post("/api/skills/preview", json={"prompt": "review api"})
    assert preview.status_code == 200
    assert preview.json()["selected"][0]["id"] == skill_id

    disabled = client.post(f"/api/skills/{skill_id}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    preview_after_disable = client.post("/api/skills/preview", json={"prompt": "review api"})
    assert preview_after_disable.status_code == 200
    assert all(item["id"] != skill_id for item in preview_after_disable.json()["selected"])


def test_formal_github_skill_update_routes(tmp_path, monkeypatch):
    from src.api.server import app
    from src.api.services import skill_github_import_service as svc
    import src.infra.config as config_module

    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(ws))
    state = {
        "commit": "abc123",
        "files": {
            "SKILL.md": "# Docs Skill\n\nRead docs.",
            "skill.json": json.dumps({"id": "docs-skill", "name": "Docs Skill", "triggers": ["docs"]}),
        },
    }
    monkeypatch.setattr(svc, "_commit_sha", lambda source, token="": state["commit"])
    monkeypatch.setattr(svc, "_discover_skill_paths", lambda source, token="": ["skills/docs-skill"])
    monkeypatch.setattr(svc, "_collect_skill_files", lambda source, base_path, token="": state["files"])
    client = TestClient(app)

    imported = client.post(
        "/api/skills/import/github",
        json={"repo_url": "https://github.com/example/skills", "path": "skills/docs-skill"},
    )
    assert imported.status_code == 200
    skill_id = imported.json()["skill"]["id"]

    state["commit"] = "def456"
    state["files"] = {
        "SKILL.md": "# Docs Skill\n\nRead docs carefully.",
        "skill.json": json.dumps({"id": "docs-skill", "name": "Docs Skill", "triggers": ["docs", "readme"]}),
    }
    check = client.post(f"/api/skills/{skill_id}/updates/check", json={})
    assert check.status_code == 200
    assert check.json()["changed"] is True

    preview = client.post(f"/api/skills/{skill_id}/updates/preview", json={})
    assert preview.status_code == 200
    assert preview.json()["latest_commit"] == "def456"

    rejected = client.post(f"/api/skills/{skill_id}/updates/apply", json={"confirmed": False})
    assert rejected.status_code == 400

    applied = client.post(f"/api/skills/{skill_id}/updates/apply", json={"confirmed": True})
    assert applied.status_code == 200
    assert applied.json()["skill"]["source"]["commit"] == "def456"
