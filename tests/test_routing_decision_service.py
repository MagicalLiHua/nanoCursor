from __future__ import annotations

from src.api.services.intent_router import classify_user_intent
from src.api.services.routing_decision_service import build_routing_decision
from src.api.services.skill_registry_service import import_skill


def test_routing_decision_keeps_greeting_lead_only(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    import_skill(
        "Python Dev",
        "# Python Dev\n\nUse pytest.",
        str(workspace),
        skill_json={"id": "python-dev", "triggers": ["python"]},
    )

    decision = build_routing_decision(
        "你好",
        workspace_dir=str(workspace),
        intent_decision=classify_user_intent("你好"),
        team=[{"role": "coder"}],
    )

    assert decision["next_action"] == "answer_directly"
    assert decision["execution_route"] == "lead_direct_reply"
    assert decision["skills"] == []
    assert decision["mcp_plan"] == []
    assert decision["agents"] == [
        {
            "role": "Lead",
            "name": "Lead",
            "temporary": False,
            "tool_permissions": ["read_only"],
            "reason": "lead_direct_reply",
        }
    ]


def test_routing_decision_selects_relevant_skill_for_code_task(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    import_skill(
        "Python Dev",
        "# Python Dev\n\nUse pytest.",
        str(workspace),
        skill_json={
            "id": "python-dev",
            "triggers": ["python", "pytest"],
            "agent_roles": ["coder", "tester"],
            "tool_permissions": ["read_only", "safe_write", "shell_safe"],
        },
    )

    prompt = "帮我用 python 写排序算法并补 pytest"
    decision = build_routing_decision(
        prompt,
        workspace_dir=str(workspace),
        intent_decision=classify_user_intent(prompt),
        team=[{"role": "coder", "name": "Coder"}],
    )

    assert decision["next_action"] == "create_agents"
    assert decision["requires"]["workspace_write"] is True
    assert decision["skills"][0]["id"] == "skill.python-dev"
    assert "safe_write" in decision["skills"][0]["tool_permissions"]
    assert any(agent["role"] == "Coder" for agent in decision["agents"])


def test_routing_decision_escalates_high_risk_to_approval(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt = "帮我删除 node_modules 并 git push"

    decision = build_routing_decision(
        prompt,
        workspace_dir=str(workspace),
        intent_decision=classify_user_intent(prompt),
    )

    assert decision["route"] == "risky_operation"
    assert decision["risk"] == "high"
    assert decision["next_action"] == "request_approval"
    assert decision["requires"]["approval"] is True


def test_routing_decision_includes_mcp_plan_for_github_context(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt = "用 GitHub issue 信息分析这个需求"

    decision = build_routing_decision(
        prompt,
        workspace_dir=str(workspace),
        intent_decision=classify_user_intent(prompt),
    )

    assert decision["route"] == "read_only"
    assert decision["next_action"] in {"inspect_files", "select_mcp_tools"}
    assert any(item["server_id"] == "mcp.github" for item in decision["mcp_plan"])
    assert decision["summary"]["mcp_count"] >= 1
