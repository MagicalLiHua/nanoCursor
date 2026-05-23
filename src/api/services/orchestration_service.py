"""Dynamic AgentHub execution orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.strategy.planner import get_strategy_definition, get_tool_policy, select_strategy
from src.api.services.capability_service import build_mcp_execution_plan


ROLE_ALIASES = {
    "lead": {"lead", "leader", "supervisor", "总控", "协调"},
    "planner": {"planner", "plan", "需求", "规划"},
    "coder": {"coder", "code", "developer", "engineer", "开发", "实现"},
    "tester": {"tester", "test", "verifier", "qa", "测试", "验证"},
    "reviewer": {"reviewer", "review", "审查", "复核"},
    "designer": {"designer", "design", "ui", "ux", "设计"},
    "devops": {"devops", "deploy", "ops", "sre", "运维", "部署"},
}

CAPABILITY_TOOL_MAP = {
    "tool.file_ops": ["read_file", "write_file", "edit_file"],
    "tool.project_index": ["project_context", "search_codebase", "list_directory", "read_file"],
    "tool.memory": ["recall_memories", "add_memory"],
    "tool.recovery": ["bash"],
    "skill.frontend-polish": ["read_file", "edit_file", "write_file"],
    "skill.delivery-review": ["bash", "task_update"],
    "mcp.docs": ["mcp_call", "project_context", "search_codebase"],
    "mcp.figma": ["mcp_call", "read_file"],
    "mcp.github": ["mcp_call", "bash"],
}

BASE_RECOMMENDED_TOOLS = ["task_create", "task_update", "task_list"]

BUILTIN_SKILL_PLAYBOOKS = {
    "skill.frontend-polish": (
        "前端体验打磨 Skill：优先检查信息密度、视觉层级、交互连续性、响应式布局、"
        "按钮与折叠控件的位置稳定性。修改后需要验证关键路径是否仍可输入、运行、折叠和恢复。"
    ),
    "skill.delivery-review": (
        "交付复核 Skill：围绕需求覆盖、测试证据、Diff 风险、未完成项和下一步建议进行复核。"
        "最终回复需要明确验证结果，无法验证时说明原因与替代判断。"
    ),
}


def _member_text(member: dict[str, Any]) -> str:
    parts = [
        member.get("name", ""),
        member.get("role", ""),
        member.get("goal", ""),
        " ".join(member.get("capabilities", []) if isinstance(member.get("capabilities"), list) else []),
    ]
    return " ".join(str(part).lower() for part in parts if part)


def _matches_role(member: dict[str, Any], role: str) -> bool:
    text = _member_text(member)
    return any(alias in text for alias in ROLE_ALIASES.get(role, {role}))


def _find_member(team: list[dict[str, Any]], role: str, fallback: str) -> dict[str, Any]:
    for member in team:
        if _matches_role(member, role):
            return member
    return {"name": fallback, "role": role, "capabilities": [], "source": "fallback"}


def _capabilities_for(member: dict[str, Any], defaults: list[str] | None = None) -> list[str]:
    capabilities = member.get("capabilities") if isinstance(member.get("capabilities"), list) else []
    result: list[str] = []
    for capability in [*(defaults or []), *capabilities]:
        text = str(capability).strip()
        if text and text not in result:
            result.append(text)
    return result[:6]


def _stage(
    stage_id: str,
    title: str,
    owner: dict[str, Any],
    description: str,
    capabilities: list[str] | None = None,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "title": title,
        "owner": owner.get("name") or owner.get("role") or "Agent",
        "owner_role": owner.get("role") or "agent",
        "description": description,
        "capabilities": _capabilities_for(owner, capabilities),
        "required": required,
    }


def _risk(level: str, title: str, detail: str) -> dict[str, str]:
    return {"level": level, "title": title, "detail": detail}


def _workspace_path(workspace_dir: str | None) -> Path | None:
    if not workspace_dir:
        return None
    try:
        return Path(workspace_dir).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def _capability_ids_from_stages(stages: list[dict[str, Any]]) -> list[str]:
    capability_ids: list[str] = []
    for stage in stages:
        for capability in stage.get("capabilities", []):
            text = str(capability).strip()
            if text and text not in capability_ids:
                capability_ids.append(text)
    return capability_ids


def build_tool_policy(capability_ids: list[str] | None = None) -> dict[str, Any]:
    """Build a recommend-only tool policy from planned capabilities."""
    recommended_tools = list(BASE_RECOMMENDED_TOOLS)
    unmatched: list[str] = []
    planned_mcp: list[str] = []

    for capability_id in capability_ids or []:
        tools = CAPABILITY_TOOL_MAP.get(capability_id)
        if not tools:
            unmatched.append(capability_id)
            continue
        if capability_id.startswith("mcp."):
            planned_mcp.append(capability_id)
        for tool in tools:
            if tool not in recommended_tools:
                recommended_tools.append(tool)

    notes = ["当前策略为 recommend_only：优先推荐这些工具，但不硬性阻断其他必要工具。"]
    if unmatched:
        notes.append(f"未匹配到工具映射的能力: {', '.join(unmatched[:8])}。")
    if planned_mcp:
        notes.append(f"MCP 能力 {', '.join(planned_mcp[:5])} 会优先通过 mcp_call 使用；不可用时再使用本地工具兜底。")

    return {
        "mode": "recommend_only",
        "recommended_tools": recommended_tools,
        "capability_count": len(capability_ids or []),
        "unmatched_capabilities": unmatched,
        "notes": notes,
    }


def _skill_candidates(workspace: Path, skill_slug: str) -> list[Path]:
    return [
        workspace / ".nanocursor" / "skills" / skill_slug / "SKILL.md",
        workspace / "skills" / skill_slug / "SKILL.md",
    ]


def _truncate_skill_content(content: str, max_chars: int) -> str:
    text = " ".join(line.rstrip() for line in content.strip().splitlines() if line.strip())
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 20].rstrip()}...（已截断）"


def load_skill_context(
    capability_ids: list[str] | None = None,
    workspace_dir: str | None = None,
    max_skill_chars: int = 1200,
    max_total_chars: int = 3600,
) -> list[dict[str, str]]:
    """Load compact skill instructions for capabilities used by a plan."""
    workspace = _workspace_path(workspace_dir)
    contexts: list[dict[str, str]] = []
    total_chars = 0

    for capability_id in capability_ids or []:
        if not capability_id.startswith("skill."):
            continue

        source = "builtin"
        content = BUILTIN_SKILL_PLAYBOOKS.get(capability_id)

        if content is None and workspace is not None:
            slug = capability_id.removeprefix("skill.")
            for candidate in _skill_candidates(workspace, slug):
                try:
                    if candidate.is_file():
                        content = candidate.read_text(encoding="utf-8")
                        try:
                            source = str(candidate.relative_to(workspace))
                        except ValueError:
                            source = str(candidate)
                        break
                except OSError:
                    continue

        if not content:
            continue

        remaining = max_total_chars - total_chars
        if remaining <= 0:
            break
        snippet = _truncate_skill_content(content, min(max_skill_chars, remaining))
        total_chars += len(snippet)
        contexts.append({"id": capability_id, "source": source, "content": snippet})

    return contexts


def build_execution_plan(
    prompt: str,
    team: list[dict[str, Any]] | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Build a team-aware execution plan for one run."""
    members = list(team or [])
    lead = _find_member(members, "lead", "Lead")
    planner = _find_member(members, "planner", "Planner")
    coder = _find_member(members, "coder", "Coder")
    tester = _find_member(members, "tester", "Tester")

    stages = [
        _stage(
            "intake",
            "接收需求与上下文定界",
            lead,
            "确认本轮任务目标、工作区边界和需要保留的用户约束。",
            ["tool.memory"],
        ),
        _stage(
            "plan",
            "任务拆解与验收标准",
            planner,
            "把需求拆成可执行任务、验收点和风险控制点。",
            ["tool.project_index"],
        ),
        _stage(
            "implement",
            "代码实现与文件变更",
            coder,
            "按计划执行代码修改，并保持变更可追踪。",
            ["tool.file_ops", "tool.project_index"],
        ),
        _stage(
            "verify",
            "验证与交付复核",
            tester,
            "运行检查、复核需求覆盖，并整理交付证据。",
            ["skill.delivery-review", "tool.recovery"],
        ),
    ]

    risks: list[dict[str, str]] = []

    if any(_matches_role(member, "designer") for member in members):
        designer = _find_member(members, "designer", "Designer")
        stages.insert(
            2,
            _stage(
                "design_review",
                "界面体验复核",
                designer,
                "检查布局密度、视觉层级、交互连续性和前端偏好一致性。",
                ["skill.frontend-polish", "mcp.figma"],
                required=False,
            ),
        )

    if any(_matches_role(member, "reviewer") for member in members):
        reviewer = _find_member(members, "reviewer", "Reviewer")
        stages.append(
            _stage(
                "diff_review",
                "Diff 风险审查",
                reviewer,
                "复核变更范围、潜在回归、质量证据和交付报告可信度。",
                ["skill.delivery-review", "tool.project_index"],
                required=False,
            )
        )

    if any(_matches_role(member, "devops") for member in members):
        devops = _find_member(members, "devops", "DevOps")
        stages.append(
            _stage(
                "environment_check",
                "环境与构建检查",
                devops,
                "检查构建命令、运行环境、部署风险和恢复路径。",
                ["tool.recovery", "mcp.github"],
                required=False,
            )
        )

    if not any(_matches_role(member, "tester") for member in members):
        risks.append(_risk("medium", "缺少测试 Agent", "当前团队没有明显 Tester / QA 角色，验证阶段会由 Lead 兜底。"))
    if not any(_matches_role(member, "coder") for member in members):
        risks.append(_risk("high", "缺少实现 Agent", "当前团队没有明显 Coder / Developer 角色，代码实现能力不足。"))
    if any("planned" in str(capability).lower() for member in members for capability in member.get("capabilities", [])):
        risks.append(_risk("medium", "存在待接入能力", "部分能力尚未配置，执行时会使用本地工具和结构化提示兜底。"))
    if not risks:
        risks.append(_risk("low", "常规交付风险", "按团队编排执行，重点关注 Diff、测试和需求覆盖。"))

    strategy_id = select_strategy(prompt)
    strategy_def = get_strategy_definition(strategy_id)

    capability_ids = _capability_ids_from_stages(stages)
    tool_policy_obj = get_tool_policy(strategy_id)
    tool_policy = {**tool_policy_obj.to_dict(), **build_tool_policy(capability_ids)}
    skill_context = load_skill_context(capability_ids, workspace_dir)
    mcp_plan = build_mcp_execution_plan(capability_ids, workspace_dir=workspace_dir)

    return {
        "prompt": prompt,
        "workspace_dir": workspace_dir,
        "strategy": strategy_id,
        "strategy_definition": strategy_def,
        "agents": [member.get("name") or member.get("role") or "Agent" for member in members],
        "stages": stages,
        "tasks": tasks_from_execution_plan(stages),
        "risks": risks,
        "capabilities": capability_ids,
        "tool_policy": tool_policy,
        "skill_context": skill_context,
        "mcp_plan": mcp_plan,
        "summary": {
            "agent_count": len(members),
            "stage_count": len(stages),
            "capability_count": len(capability_ids),
            "recommended_tool_count": len(tool_policy["recommended_tools"]),
            "skill_context_count": len(skill_context),
            "mcp_count": len(mcp_plan),
            "usable_mcp_count": sum(1 for item in mcp_plan if item.get("usable")),
            "risk_count": len(risks),
            "optional_stage_count": sum(1 for stage in stages if not stage.get("required", True)),
        },
    }


def tasks_from_execution_plan(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert execution stages into frontend task cards."""
    tasks: list[dict[str, Any]] = []
    for index, stage in enumerate(stages, start=1):
        tasks.append(
            {
                "id": f"stage-{index:02d}-{stage['id']}",
                "title": stage["title"],
                "description": stage.get("description", ""),
                "status": "pending",
                "owner": stage.get("owner", "Agent"),
                "capabilities": stage.get("capabilities", []),
                "dependencies": [] if index == 1 else [f"stage-{index - 1:02d}-{stages[index - 2]['id']}"],
            }
        )
    return tasks


def build_runtime_instructions(execution_plan: dict[str, Any] | None, team: list[dict[str, Any]] | None = None) -> str:
    """Convert an execution plan into system-prompt instructions."""
    if not execution_plan:
        return ""

    stages = execution_plan.get("stages") if isinstance(execution_plan.get("stages"), list) else []
    risks = execution_plan.get("risks") if isinstance(execution_plan.get("risks"), list) else []
    tool_policy = execution_plan.get("tool_policy") if isinstance(execution_plan.get("tool_policy"), dict) else {}
    skill_context = execution_plan.get("skill_context") if isinstance(execution_plan.get("skill_context"), list) else []
    mcp_plan = execution_plan.get("mcp_plan") if isinstance(execution_plan.get("mcp_plan"), list) else []
    members = list(team or [])

    lines = [
        "",
        "【AgentHub 动态执行编排】",
        f"- 执行策略: {execution_plan.get('strategy', 'team_aware_run_per_message')}",
        "- 你必须按下列阶段推进，并在关键节点使用 task_create / task_update 记录任务状态。",
    ]

    if members:
        lines.append("- 本轮团队:")
        for member in members:
            capabilities = member.get("capabilities") if isinstance(member.get("capabilities"), list) else []
            capability_text = ", ".join(str(item) for item in capabilities[:5]) or "未声明能力"
            lines.append(
                f"  - {member.get('name', 'Agent')} ({member.get('role', 'agent')}): "
                f"{member.get('goal', '') or '参与本轮交付'}；能力: {capability_text}"
            )

    if stages:
        lines.append("- 阶段顺序:")
        for index, stage in enumerate(stages, start=1):
            capabilities = ", ".join(str(item) for item in stage.get("capabilities", [])[:5]) or "通用能力"
            requirement = "必须" if stage.get("required", True) else "按需"
            lines.append(
                f"  {index}. [{requirement}] {stage.get('title')} "
                f"— 负责人: {stage.get('owner')}；能力: {capabilities}；"
                f"{stage.get('description', '')}"
            )

    if risks:
        lines.append("- 风险约束:")
        for risk in risks[:5]:
            lines.append(f"  - {risk.get('level', 'low')}: {risk.get('title')}。{risk.get('detail', '')}")

    recommended_tools = tool_policy.get("recommended_tools") if isinstance(tool_policy.get("recommended_tools"), list) else []
    if recommended_tools:
        lines.append("- 推荐工具策略:")
        lines.append(f"  - 模式: {tool_policy.get('mode', 'recommend_only')}")
        lines.append(f"  - 优先工具: {', '.join(str(tool) for tool in recommended_tools[:14])}")
        for note in tool_policy.get("notes", [])[:3] if isinstance(tool_policy.get("notes"), list) else []:
            lines.append(f"  - {note}")

    if skill_context:
        lines.append("- Skill 上下文摘录:")
        for item in skill_context[:4]:
            lines.append(f"  - {item.get('id', 'skill')} ({item.get('source', 'unknown')}): {item.get('content', '')}")

    if mcp_plan:
        lines.append("- MCP 使用计划:")
        for item in mcp_plan[:5]:
            tools = item.get("tools") if isinstance(item.get("tools"), list) else []
            tool_names = ", ".join(str(tool.get("name", "")) for tool in tools[:5] if isinstance(tool, dict) and tool.get("name"))
            state = "可用" if item.get("usable") else "不可用/待配置"
            suffix = f"；可用工具: {tool_names}" if tool_names else ""
            lines.append(
                f"  - {item.get('server_id')}: {state}；状态: {item.get('status', 'unknown')}；"
                f"{item.get('reason', '')}{suffix}"
            )

    lines.extend(
        [
            "- 工具使用策略:",
            "  - 开始实现前，先用 task_create 为每个阶段创建任务。",
            "  - 需要理解项目时优先使用 project_context / search_codebase / list_directory / read_file。",
            "  - MCP 计划中标记为可用的 server，可以在用户审批后通过 mcp_call 调用；不可用或未配置时不要硬调用。",
            "  - 写代码时优先使用 write_file / edit_file，并保持变更小而可审查。",
            "  - 验证阶段必须尝试运行合适的检查命令；若无法运行，要说明原因和替代验证方式。",
            "  - 如果团队包含 Reviewer，需要在最终回复中单独说明 Diff 风险与复核结论。",
            "  - 如果团队包含 Designer，需要在最终回复中单独说明 UI/交互复核结论。",
            "  - 如果团队包含 DevOps，需要在最终回复中单独说明环境、构建或部署风险。",
            "- 最终回复必须包含：完成内容、验证结果、风险/缺口、下一步建议。",
        ]
    )
    return "\n".join(lines)
