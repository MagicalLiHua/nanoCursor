"""Skill runtime tests — selection, instruction building, manifest validation."""

import pytest

from src.agent.skill_runtime import select_skills_for_run, build_skill_instruction
from src.api.services.skill_manifest_service import (
    validate_skill_manifest,
    parse_skill_manifest,
)


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

class TestManifestValidation:
    def test_empty_content_fails(self):
        result = validate_skill_manifest("")
        assert result["ok"] is False

    def test_missing_name_fails(self):
        content = """---
description: Does API review
agents:
  - reviewer
---
Do the review."""
        result = validate_skill_manifest(content)
        name_checks = [c for c in result["checks"] if c["id"] == "name"]
        assert any(c["status"] == "failed" for c in name_checks)

    def test_valid_manifest_passes(self):
        content = """---
name: API Review
version: 0.1.0
description: Review API compatibility
agents:
  - reviewer
  - tester
capabilities:
  - tool.project_index
  - tool.file_ops
risk_level: medium
---
Review the API."""
        result = validate_skill_manifest(content)
        assert result["ok"] is True

    def test_invalid_version_warns(self):
        content = """---
name: My Skill
version: latest
agents:
  - coder
---
Body."""
        result = validate_skill_manifest(content)
        version_checks = [c for c in result["checks"] if c["id"] == "version"]
        assert any(c["status"] == "warning" for c in version_checks)

    def test_invalid_risk_level_warns(self):
        content = """---
name: My Skill
risk_level: extreme
---
Body."""
        result = validate_skill_manifest(content)
        risk_checks = [c for c in result["checks"] if c["id"] == "risk_level"]
        assert any(c["status"] == "warning" for c in risk_checks)

    def test_unknown_capability_warns(self):
        content = """---
name: My Skill
capabilities:
  - unknown.thing
  - tool.file_ops
---
Body."""
        result = validate_skill_manifest(content)
        cap_checks = [c for c in result["checks"] if c["id"] == "capabilities"]
        assert any(c["status"] == "warning" for c in cap_checks)
        # The check message should mention the unknown capability
        msg = next((c["message"] for c in cap_checks if c["status"] == "warning"), "")
        assert "unknown.thing" in msg

    def test_empty_agents_list_warns(self):
        content = """---
name: My Skill
agents: []
---
Body."""
        result = validate_skill_manifest(content)
        agent_checks = [c for c in result["checks"] if c["id"] == "agents"]
        assert any(c["status"] == "warning" for c in agent_checks)

    def test_no_frontmatter_is_ok(self):
        content = "Just a skill without frontmatter."
        result = validate_skill_manifest(content)
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# Skill selection
# ---------------------------------------------------------------------------

class TestSkillSelection:
    def test_no_skills_dir_returns_empty(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        skills = select_skills_for_run("fix bug", workspace_dir=str(ws))
        assert skills == []

    def test_keyword_match_selects_skill(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        skill_dir = ws / ".nanocursor" / "skills" / "api-review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("""---
name: API Review
description: Review API compatibility and failure cases.
agents:
  - reviewer
---
Review the API changes for compatibility issues.""")

        skills = select_skills_for_run("review the API changes", workspace_dir=str(ws))
        assert any(skill["name"] == "API Review" for skill in skills)

    def test_team_role_match_boosts_relevant_skill_but_does_not_trigger_alone(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        skill_dir = ws / ".nanocursor" / "skills" / "code-review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("""---
name: Code Review
agents:
  - reviewer
---
Review code quality.""")

        skills = select_skills_for_run(
            "do something",
            team=[{"role": "reviewer"}],
            workspace_dir=str(ws),
        )
        assert all(skill["name"] != "Code Review" for skill in skills)

        skills = select_skills_for_run(
            "review code quality",
            team=[{"role": "reviewer"}],
            workspace_dir=str(ws),
        )
        assert any(skill["name"] == "Code Review" for skill in skills)

    def test_no_match_returns_empty(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        skill_dir = ws / ".nanocursor" / "skills" / "deploy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("""---
name: Deploy
---
Deploy to production.""")

        skills = select_skills_for_run("xyz abc lmn", workspace_dir=str(ws))
        assert len(skills) == 0

    def test_capped_at_five(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        for i in range(7):
            sd = ws / ".nanocursor" / "skills" / f"skill-{i}"
            sd.mkdir(parents=True)
            (sd / "SKILL.md").write_text(f"""---
name: Skill {i}
description: test test test
---
Body.""")

        skills = select_skills_for_run("test", workspace_dir=str(ws))
        assert len(skills) <= 5


# ---------------------------------------------------------------------------
# Skill instruction building
# ---------------------------------------------------------------------------

class TestSkillInstruction:
    def test_empty_skills_returns_empty(self):
        assert build_skill_instruction([]) == ""

    def test_builds_instruction_within_budget(self):
        skills = [
            {"name": "API Review", "description": "Review API compatibility."},
            {"name": "Code Review", "description": "Review code quality."},
        ]
        instruction = build_skill_instruction(skills, max_chars=500)
        assert "API Review" in instruction
        assert "Code Review" in instruction
        assert len(instruction) <= 500

    def test_respects_char_budget(self):
        skills = [{"name": f"Skill {i}", "description": "A" * 300} for i in range(10)]
        instruction = build_skill_instruction(skills, max_chars=200)
        assert len(instruction) <= 200
