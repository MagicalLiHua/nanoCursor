"""Start a run that is scoped to an existing conversation."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from src.api.models import ConversationRunRequest, RunRequest
from src.api.run_state import (
    active_runs,
    emit_agent_activity,
    emit_agenthub_event,
    event_store,
    get_workspace,
    runs_lock,
)
from src.api.services.conversation_service import (
    compose_runtime_team_async,
    get_conversation,
    link_run_to_conversation,
)
from src.api.services.intent_router import classify_user_intent_async
from src.api.services.intent_runtime_context import context_from_conversation
from src.api.services.orchestration_service import build_execution_plan_async
from src.api.services.run_start_service import intent_session_fields, start_standard_run


def lead_only_execution_plan(prompt: str, workspace_dir: str, team: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a minimal execution plan for lightweight Lead-only replies."""
    lead = team[0] if team else {"name": "Lead", "role": "lead"}
    stage = {
        "id": "lead_reply",
        "title": "Lead 直接回复",
        "owner": lead.get("name") or "Lead",
        "owner_role": lead.get("role") or "lead",
        "description": "判断为轻量对话，不启动完整交付流水线；由 Lead 结合当前上下文直接回复。",
        "capabilities": ["tool.memory"],
        "required": True,
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "failed_at": None,
        "tool_evidence": [],
        "failure": None,
    }
    return {
        "prompt": prompt,
        "workspace_dir": workspace_dir,
        "strategy": "lead_direct_reply",
        "strategy_definition": {
            "id": "lead_direct_reply",
            "label": "Lead direct reply",
            "description": "轻量消息只由 Lead 处理，避免每次都走完整 Planner/Coder/Tester 流程。",
        },
        "agents": [lead.get("name") or "Lead"],
        "stages": [stage],
        "tasks": [
            {
                "id": "stage-01-lead_reply",
                "title": stage["title"],
                "description": stage["description"],
                "status": "pending",
                "owner": stage["owner"],
                "capabilities": stage["capabilities"],
                "dependencies": [],
            }
        ],
        "risks": [],
        "capabilities": ["tool.memory"],
        "tool_policy": {
            "mode": "enforced",
            "allowed_tools": ["recall_memories"],
            "denied_tools": ["bash", "write_file", "edit_file", "delete_file", "mcp_call", "spawn_agent"],
            "approval_required": [],
            "approval_required_levels": ["risky_write", "shell_risky", "external_risky", "mcp_write"],
            "recommended_tools": [],
            "budgets": {"max_tool_calls": 4, "max_file_writes": 0, "max_test_runs": 0},
            "notes": ["轻量对话默认不执行文件修改和命令。"],
        },
        "skill_context": [],
        "mcp_plan": [],
        "summary": {
            "agent_count": len(team) or 1,
            "stage_count": 1,
            "capability_count": 1,
            "recommended_tool_count": 0,
            "skill_context_count": 0,
            "mcp_count": 0,
            "usable_mcp_count": 0,
            "risk_count": 0,
            "optional_stage_count": 0,
        },
    }


def align_tool_policy_with_intent(execution_plan: dict[str, Any], intent_decision: dict[str, Any]) -> dict[str, Any]:
    """Let the normalized intent tighten or widen the plan's tool policy.

    Strategy selection can legitimately choose ``analysis_only`` for read-only
    work, but a test-only intent still needs safe shell/test tools. Keep writes
    disabled while allowing explicit verification commands.
    """
    policy = execution_plan.setdefault("tool_policy", {})
    allowed = list(policy.get("allowed_tools") or [])
    denied = list(policy.get("denied_tools") or [])
    recommended = list(policy.get("recommended_tools") or [])
    budgets = policy.setdefault("budgets", {})

    if intent_decision.get("requires_shell") and not intent_decision.get("requires_workspace_write"):
        for tool in ("bash", "run_tests"):
            if tool not in allowed:
                allowed.append(tool)
            if tool not in recommended:
                recommended.append(tool)
        denied = [tool for tool in denied if tool not in {"bash", "run_tests"}]
        budgets["max_test_runs"] = max(int(budgets.get("max_test_runs") or 0), 3)
        budgets["max_file_writes"] = 0
        notes = list(policy.get("notes") or [])
        note = "只读验证任务允许 run_tests 和 shell_safe 命令，但仍禁止文件写入。"
        if note not in notes:
            notes.append(note)
        policy["notes"] = notes

    if not intent_decision.get("requires_workspace_write"):
        for tool in ("write_file", "edit_file", "delete_file", "apply_patch"):
            if tool in allowed:
                allowed.remove(tool)
            if tool not in denied:
                denied.append(tool)
        budgets["max_file_writes"] = 0

    policy["allowed_tools"] = allowed
    policy["denied_tools"] = denied
    policy["recommended_tools"] = recommended
    return execution_plan


async def start_conversation_run(
    conversation_id: str,
    request: ConversationRunRequest,
) -> dict[str, Any]:
    """Compose and start one run for an existing conversation."""
    conversation = get_conversation(conversation_id, request.workspace_dir or get_workspace())
    if not conversation:
        raise HTTPException(status_code=404, detail="未找到该会话")

    workspace_dir = request.workspace_dir or conversation["workspace_dir"]
    team = conversation.get("team", {})
    members = list(team.get("members", []))
    runtime_team_source = team.get("source", "conversation")
    intent_context = context_from_conversation(
        conversation,
        prompt=request.prompt,
        workspace_dir=workspace_dir,
    )
    intent_decision = await classify_user_intent_async(
        request.prompt,
        conversation_summary=str(conversation.get("conversation_summary") or ""),
        runtime_context=intent_context,
    )
    is_simple = intent_decision.get("execution_route") == "lead_direct_reply"
    runtime_composition = await compose_runtime_team_async(
        request.prompt,
        workspace_dir,
        conversation_id,
        intent_decision=intent_decision,
        runtime_context=intent_context,
    )
    if is_simple:
        lead_member = next((member for member in members if str(member.get("role", "")).lower() == "lead"), None)
        members = [lead_member or {"name": "Lead", "role": "lead"}]
        runtime_team_source = "lead_direct"
        runtime_composition["members"] = members
        runtime_composition["complexity"] = {
            **dict(runtime_composition.get("complexity") or {}),
            "level": intent_decision.get("level", "simple"),
            "route": intent_decision.get("route", "direct_answer"),
            "execution_route": intent_decision.get("execution_route", "lead_direct_reply"),
            "intent": intent_decision.get("intent", "direct_answer"),
            "requires_workspace_write": intent_decision.get("requires_workspace_write", False),
            "requires_workspace_read": intent_decision.get("requires_workspace_read", False),
            "requires_shell": intent_decision.get("requires_shell", False),
            "requires_approval": intent_decision.get("requires_approval", False),
            "requires_execution": intent_decision.get("requires_execution", False),
            "intent_decision": intent_decision,
        }
    elif len(members) <= 1:
        members = list(runtime_composition.get("members", []))
        runtime_team_source = "runtime_composed"
    else:
        runtime_composition["complexity"] = {
            **dict(runtime_composition.get("complexity") or {}),
            "intent_decision": intent_decision,
            "route": intent_decision.get("route", runtime_composition.get("complexity", {}).get("route")),
            "execution_route": intent_decision.get("execution_route", "agenthub_delivery"),
        }

    execution_plan = (
        lead_only_execution_plan(request.prompt, workspace_dir, members)
        if is_simple
        else await build_execution_plan_async(
            prompt=request.prompt,
            team=members,
            workspace_dir=workspace_dir,
        )
    )
    execution_plan["complexity"] = runtime_composition.get("complexity", {})
    execution_plan["intent_decision"] = intent_decision
    align_tool_policy_with_intent(execution_plan, intent_decision)
    execution_plan.setdefault("summary", {})["runtime_team_source"] = runtime_team_source
    execution_plan.setdefault("summary", {})["intent_route"] = intent_decision.get("route")

    response = await start_standard_run(
        RunRequest(
            prompt=request.prompt,
            workspace_dir=workspace_dir,
            messages=request.messages,
            conversation_id=conversation_id,
            team=members,
            execution_plan=execution_plan,
        )
    )
    thread_id = response.thread_id
    updated = link_run_to_conversation(
        conversation_id,
        thread_id,
        workspace_dir,
        prompt=request.prompt,
        team=members,
    )

    with runs_lock:
        run_info = active_runs.get(thread_id)
        if run_info is not None:
            run_info.bind_conversation(conversation_id, members)
            run_info.set_execution_plan(execution_plan)

    event_store.update_session(
        thread_id,
        workspace_dir,
        conversation_id=conversation_id,
        team=members,
        execution_plan=execution_plan,
        agent_loop_policy=updated.get("agent_loop_policy", "run_per_message"),
        runtime_team_source=runtime_team_source,
        runtime_composition=runtime_composition,
        **intent_session_fields(intent_decision),
    )
    emit_agenthub_event(
        thread_id=thread_id,
        event_type="agent_complexity_assessed",
        title="Lead 已判断任务复杂度",
        content=runtime_composition.get("complexity", {}).get("rationale", ""),
        agent="lead",
        payload={
            "conversation_id": conversation_id,
            "complexity": runtime_composition.get("complexity", {}),
            "intent_decision": intent_decision,
            "members": members,
            "source": runtime_team_source,
        },
        workspace_dir=workspace_dir,
    )
    emit_agent_activity(
        thread_id=thread_id,
        agent="lead",
        title="Lead 已完成任务复杂度判断",
        content=runtime_composition.get("complexity", {}).get("rationale", ""),
        workspace_dir=workspace_dir,
        payload={
            "phase": "complexity_assessed",
            "complexity": runtime_composition.get("complexity", {}),
            "intent_decision": intent_decision,
            "members": [member.get("name") for member in members],
        },
    )
    emit_agenthub_event(
        thread_id=thread_id,
        event_type="team_updated",
        title="运行团队已绑定",
        content="Lead 已为本次运行准备 Agent 群组；默认会话团队仍保持轻量。",
        agent="lead",
        payload={
            "conversation_id": conversation_id,
            "members": members,
            "source": runtime_team_source,
        },
        workspace_dir=workspace_dir,
    )
    emit_agenthub_event(
        thread_id=thread_id,
        event_type="plan_created",
        title="动态执行策略已生成",
        content="nanoCursor 已根据本会话团队生成本轮执行阶段。",
        agent="lead",
        payload={
            "conversation_id": conversation_id,
            "strategy": execution_plan["strategy"],
            "stages": execution_plan["stages"],
            "tasks": execution_plan["tasks"],
            "risks": execution_plan["risks"],
            "summary": execution_plan["summary"],
        },
        workspace_dir=workspace_dir,
    )
    return {
        "run": response,
        "conversation": updated,
        "runtime_team": {"members": members, "source": runtime_team_source},
        "intent_decision": intent_decision,
    }
