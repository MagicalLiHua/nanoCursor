from src.api.services.orchestration_service import (
    build_execution_plan,
    build_runtime_instructions,
    tasks_from_execution_plan,
)


def test_execution_plan_adds_role_specific_stages():
    team = [
        {"name": "Lead", "role": "lead", "capabilities": ["tool.memory"]},
        {"name": "Planner", "role": "planner", "capabilities": ["tool.project_index"]},
        {"name": "Coder", "role": "coder", "capabilities": ["tool.file_ops"]},
        {"name": "Tester", "role": "tester", "capabilities": ["skill.delivery-review"]},
        {"name": "Designer", "role": "designer", "capabilities": ["skill.frontend-polish"]},
        {"name": "Reviewer", "role": "reviewer", "capabilities": ["skill.delivery-review"]},
        {"name": "DevOps", "role": "devops", "capabilities": ["tool.recovery"]},
    ]

    plan = build_execution_plan("帮我打磨前端并检查部署风险", team, "/tmp/workspace")
    stage_ids = [stage["id"] for stage in plan["stages"]]

    assert "design_review" in stage_ids
    assert "diff_review" in stage_ids
    assert "environment_check" in stage_ids
    assert plan["summary"]["agent_count"] == len(team)
    assert plan["summary"]["optional_stage_count"] == 3
    assert len(plan["tasks"]) == len(plan["stages"])


def test_execution_plan_includes_tool_policy_and_builtin_skill_context(tmp_path):
    team = [
        {"name": "Coder", "role": "coder", "capabilities": ["tool.file_ops"]},
        {"name": "Designer", "role": "designer", "capabilities": ["skill.frontend-polish"]},
        {"name": "Reviewer", "role": "reviewer", "capabilities": ["skill.delivery-review"]},
        {"name": "Tester", "role": "tester", "capabilities": ["skill.delivery-review"]},
    ]

    plan = build_execution_plan("打磨界面并复核交付", team, str(tmp_path))

    assert plan["tool_policy"]["mode"] == "recommend_only"
    assert "task_create" in plan["tool_policy"]["recommended_tools"]
    assert "edit_file" in plan["tool_policy"]["recommended_tools"]
    assert "bash" in plan["tool_policy"]["recommended_tools"]
    assert {item["id"] for item in plan["skill_context"]} >= {
        "skill.frontend-polish",
        "skill.delivery-review",
    }
    assert plan["summary"]["recommended_tool_count"] == len(plan["tool_policy"]["recommended_tools"])
    assert plan["summary"]["skill_context_count"] == len(plan["skill_context"])


def test_execution_plan_loads_workspace_skill_context(tmp_path):
    skill_dir = tmp_path / ".nanocursor" / "skills" / "api-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# API Review\n\n检查 API 契约、错误码、幂等性和兼容性。",
        encoding="utf-8",
    )
    team = [
        {"name": "Reviewer", "role": "reviewer", "capabilities": ["skill.api-review"]},
        {"name": "Coder", "role": "coder", "capabilities": ["tool.file_ops"]},
        {"name": "Tester", "role": "tester", "capabilities": ["skill.delivery-review"]},
    ]

    plan = build_execution_plan("复核 API 变更", team, str(tmp_path))

    workspace_skill = next(item for item in plan["skill_context"] if item["id"] == "skill.api-review")
    assert workspace_skill["source"] == ".nanocursor/skills/api-review/SKILL.md"
    assert "幂等性" in workspace_skill["content"]


def test_execution_plan_warns_when_core_roles_missing():
    plan = build_execution_plan("只做代码实现", [{"name": "Lead", "role": "lead"}], "/tmp/workspace")

    risk_titles = {risk["title"] for risk in plan["risks"]}

    assert "缺少测试 Agent" in risk_titles
    assert "缺少实现 Agent" in risk_titles


def test_tasks_from_execution_plan_links_dependencies():
    stages = [
        {"id": "a", "title": "A", "description": "first", "owner": "Lead", "capabilities": []},
        {"id": "b", "title": "B", "description": "second", "owner": "Coder", "capabilities": ["tool.file_ops"]},
    ]

    tasks = tasks_from_execution_plan(stages)

    assert tasks[0]["dependencies"] == []
    assert tasks[1]["dependencies"] == ["stage-01-a"]
    assert tasks[1]["capabilities"] == ["tool.file_ops"]


def test_runtime_instructions_include_team_stages_and_constraints():
    team = [
        {"name": "Designer", "role": "designer", "goal": "复核 UI", "capabilities": ["skill.frontend-polish"]},
        {"name": "Reviewer", "role": "reviewer", "goal": "复核 Diff", "capabilities": ["skill.delivery-review"]},
    ]
    plan = build_execution_plan("打磨前端", team, "/tmp/workspace")

    instructions = build_runtime_instructions(plan, team)

    assert "AgentHub 动态执行编排" in instructions
    assert "Designer" in instructions
    assert "界面体验复核" in instructions
    assert "Diff 风险" in instructions
    assert "task_create" in instructions
    assert "推荐工具策略" in instructions
    assert "Skill 上下文摘录" in instructions
    assert "前端体验打磨 Skill" in instructions
    assert "最终回复必须包含" in instructions
