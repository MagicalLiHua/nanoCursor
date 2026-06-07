from src.api.services.orchestration_service import (
    build_execution_plan,
    build_skill_quality_rules,
    build_runtime_instructions,
    tasks_from_execution_plan,
)
from src.api.services.conversation_run_service import align_tool_policy_with_intent


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


def test_analysis_only_plan_uses_read_only_stages(tmp_path):
    team = [
        {"name": "Lead", "role": "lead", "capabilities": ["tool.memory"]},
        {"name": "Planner", "role": "planner", "capabilities": ["tool.project_index"]},
        {"name": "Tester", "role": "tester", "capabilities": ["skill.delivery-review"]},
    ]

    plan = build_execution_plan("只分析 README，不修改任何文件", team, str(tmp_path))
    stage_ids = [stage["id"] for stage in plan["stages"]]

    assert plan["strategy"] == "analysis_only"
    assert stage_ids == ["intake", "plan"]
    assert "implement" not in stage_ids
    assert "verify" not in stage_ids
    assert all(risk["title"] != "缺少实现 Agent" for risk in plan["risks"])
    assert plan["tool_policy"]["budgets"]["max_file_writes"] == 0


def test_test_only_intent_keeps_safe_test_tools_without_write(tmp_path):
    plan = build_execution_plan(
        "请只运行当前项目的 pytest 测试并告诉我结果，不要修改文件。",
        [{"name": "Lead", "role": "lead"}, {"name": "Tester", "role": "tester"}],
        str(tmp_path),
        strategy_id="analysis_only",
    )

    align_tool_policy_with_intent(
        plan,
        {
            "route": "test_only",
            "requires_shell": True,
            "requires_workspace_write": False,
        },
    )

    assert "run_tests" in plan["tool_policy"]["allowed_tools"]
    assert "bash" in plan["tool_policy"]["allowed_tools"]
    assert "run_tests" not in plan["tool_policy"]["denied_tools"]
    assert "bash" not in plan["tool_policy"]["denied_tools"]
    assert "write_file" in plan["tool_policy"]["denied_tools"]
    assert plan["tool_policy"]["budgets"]["max_file_writes"] == 0
    assert plan["tool_policy"]["budgets"]["max_test_runs"] >= 1


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
    assert plan["skill_quality_rules"]
    assert plan["summary"]["skill_quality_rule_count"] >= 1
    assert plan["summary"]["recommended_tool_count"] == len(plan["tool_policy"]["recommended_tools"])
    assert plan["summary"]["skill_context_count"] == len(plan["skill_context"])


def test_execution_plan_recommends_mcp_call_for_mcp_capability(tmp_path):
    team = [
        {"name": "Reviewer", "role": "reviewer", "capabilities": ["mcp.github", "skill.delivery-review"]},
        {"name": "Coder", "role": "coder", "capabilities": ["tool.file_ops"]},
        {"name": "Tester", "role": "tester", "capabilities": ["skill.delivery-review"]},
    ]

    plan = build_execution_plan("查看 GitHub PR 并复核代码", team, str(tmp_path))
    instructions = build_runtime_instructions(plan, team)

    assert "mcp_call" in plan["tool_policy"]["recommended_tools"]
    assert any(item["server_id"] == "mcp.github" for item in plan["mcp_plan"])
    assert plan["summary"]["mcp_count"] >= 1
    assert "MCP 使用计划" in instructions
    assert "mcp_call" in instructions


def test_execution_plan_loads_workspace_skill_context(tmp_path):
    from src.api.services.skill_registry_service import import_skill

    import_skill(
        "API Review",
        "# API Review\n\n检查 API 契约、错误码、幂等性和兼容性。",
        str(tmp_path),
        skill_json={
            "id": "api-review",
            "agent_roles": ["reviewer"],
            "triggers": ["api"],
            "quality_rules": ["Reviewer 必须检查 API 契约、错误码和幂等性。"],
            "tool_permissions": ["read_only"],
        },
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
    assert workspace_skill["quality_rules"] == ["Reviewer 必须检查 API 契约、错误码和幂等性。"]
    assert any(
        rule["skill_id"] == "skill.api-review"
        for stage in plan["skill_quality_rules"]
        for rule in stage["rules"]
    )


def test_skill_quality_rules_are_role_specific_in_runtime_instructions(tmp_path):
    from src.api.services.skill_registry_service import import_skill

    import_skill(
        "API Review",
        "# API Review\n\nReview API contracts.",
        str(tmp_path),
        skill_json={
            "id": "api-review",
            "agent_roles": ["reviewer"],
            "quality_rules": ["Reviewer 必须检查 API 兼容性。"],
            "tool_permissions": ["read_only"],
        },
    )
    team = [
        {"name": "Reviewer", "role": "reviewer", "capabilities": ["skill.api-review"]},
        {"name": "Coder", "role": "coder", "capabilities": ["tool.file_ops"]},
        {"name": "Tester", "role": "tester", "capabilities": ["skill.delivery-review"]},
    ]

    plan = build_execution_plan("复核 API 变更", team, str(tmp_path))
    instructions = build_runtime_instructions(plan, team)

    reviewer_rules = [
        item for item in plan["skill_quality_rules"]
        if item["owner_role"] == "reviewer"
    ]
    coder_rules = [
        item for item in plan["skill_quality_rules"]
        if item["owner_role"] == "coder"
    ]

    assert reviewer_rules
    assert any(
        rule["rule"] == "Reviewer 必须检查 API 兼容性。"
        for item in reviewer_rules
        for rule in item["rules"]
    )
    assert not any(
        rule["skill_id"] == "skill.api-review"
        for item in coder_rules
        for rule in item["rules"]
    )
    assert "Skill 角色质量标准" in instructions
    assert "Reviewer 必须检查 API 兼容性" in instructions


def test_build_skill_quality_rules_matches_stage_capability_even_without_role_match():
    stages = [
        {
            "id": "verify",
            "title": "验证",
            "owner": "Lead",
            "owner_role": "lead",
            "capabilities": ["skill.delivery-review"],
        }
    ]
    skill_context = [
        {
            "id": "skill.delivery-review",
            "source": "builtin",
            "agent_roles": ["tester"],
            "quality_rules": ["必须整理验证证据。"],
        }
    ]

    result = build_skill_quality_rules(stages, skill_context)

    assert result[0]["rules"][0]["rule"] == "必须整理验证证据。"


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

    assert "nanoCursor 动态执行编排" in instructions
    assert "Designer" in instructions
    assert "界面体验复核" in instructions
    assert "Diff 风险" in instructions
    assert "task_create" in instructions
    assert "推荐工具策略" in instructions
    assert "Skill 上下文摘录" in instructions
    assert "前端体验打磨 Skill" in instructions
    assert "最终回复必须包含" in instructions
