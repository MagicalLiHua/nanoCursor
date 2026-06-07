"""Prepare prompts and optional parallel briefing context for a runtime run."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from src.agent.prompt_builder import _build_core
from src.api.services.agent_loop_state_service import append_loop_step
from src.api.services.context_service import build_context_pack
from src.api.services.orchestration_service import build_runtime_instructions
from src.api.services.run_state_service import save_run_context_pack


EmitEvent = Callable[..., Any]
EmitActivity = Callable[..., Any]
ParallelBriefingRunner = Callable[..., Awaitable[dict[str, Any]]]


def prepare_runtime_system(
    *,
    thread_id: str,
    workspace_dir: str,
    messages: list[dict[str, Any]],
    execution_plan: dict[str, Any],
    run_team: list[dict[str, Any]],
    conversation_id: str | None,
    is_lead_direct_run: bool,
    uses_runtime_turn_loop: bool,
    workdir: str,
    event_store: Any,
    emit_event: EmitEvent,
    emit_activity: EmitActivity,
) -> str:
    """Build the base system prompt, orchestration instructions, and context pack."""
    strategy = execution_plan.get("strategy", "feature_delivery")
    system = _build_core(strategy)
    system = f"{system}\n\n注意：工作目录已经是 {workdir}，写文件名时直接用文件名，不要加 workspace/ 前缀。"
    runtime_instructions = build_runtime_instructions(execution_plan, run_team)
    if runtime_instructions:
        system = f"{system}\n{runtime_instructions}"
        emit_event(
            thread_id=thread_id,
            event_type="orchestration_applied",
            title="动态编排已注入 Runtime",
            content="本次运行将按团队执行策略约束 Agent 的阶段、能力和验证要求。",
            agent="lead",
            payload={
                "strategy": execution_plan.get("strategy"),
                "stage_count": len(execution_plan.get("stages", [])),
                "team_count": len(run_team),
                "runtime_instruction_length": len(runtime_instructions),
            },
            workspace_dir=workspace_dir,
        )
    emit_activity(
        thread_id=thread_id,
        agent="lead",
        title="Lead 正在判断任务复杂度",
        content="正在结合执行策略、团队配置、上下文包和工具权限决定本轮怎么推进。",
        workspace_dir=workspace_dir,
        payload={
            "phase": "complexity_assessment",
            "strategy": execution_plan.get("strategy"),
            "complexity": execution_plan.get("complexity", {}),
        },
    )
    try:
        context_pack = build_context_pack(
            prompt=str(messages[-1].get("content", "")) if messages else "",
            team=run_team,
            workspace_dir=workspace_dir,
            execution_plan=execution_plan,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        system = f"{system}\n\n{context_pack.to_text()}"
        context_data = context_pack.to_dict()
        event_store.update_session(thread_id, workspace_dir, context_pack=context_data)
        try:
            save_run_context_pack(thread_id, workspace_dir, context_data)
        except Exception:
            pass
        emit_event(
            thread_id=thread_id,
            event_type="context_pack_built",
            title="上下文包已构建",
            content="已注入会话摘要、运行摘要、相关文件、最近变更、文件大纲和当前计划。",
            agent="system",
            payload={
                "relevant_files": context_pack.relevant_files,
                "recent_changes": context_pack.recent_changes,
                "file_outline_count": len(context_pack.file_outlines),
                "selected_skills": context_data.get("selected_skill_details", []),
                "omitted_skills": context_data.get("omitted_skills", []),
                "skill_budget": context_data.get("skill_budget", {}),
                "estimated_tokens": context_pack.estimate_tokens(),
            },
            workspace_dir=workspace_dir,
        )
        if context_data.get("selected_skill_details") or context_data.get("omitted_skills"):
            selected = context_data.get("selected_skill_details", [])
            omitted = context_data.get("omitted_skills", [])
            emit_event(
                thread_id=thread_id,
                event_type="skill_context_selected",
                title="Skills 选择已记录",
                content=f"本轮选择 {len(selected)} 个 Skill，忽略 {len(omitted)} 个候选 Skill。",
                agent="system",
                payload={
                    "selected_skills": selected,
                    "omitted_skills": omitted,
                    "skill_budget": context_data.get("skill_budget", {}),
                },
                workspace_dir=workspace_dir,
            )
        try:
            if not is_lead_direct_run and not uses_runtime_turn_loop:
                append_loop_step(
                    thread_id,
                    workspace_dir,
                    phase="observe",
                    action={
                        "type": "inspect_project",
                        "goal": "Build and inject the run context pack.",
                        "agent": "Lead",
                        "context_requirements": {
                            "selected_files": context_pack.relevant_files,
                            "file_outline_count": len(context_pack.file_outlines),
                        },
                    },
                    context_pack_id="run_context_pack",
                    summary=f"Selected {len(context_pack.relevant_files)} files for this step.",
                )
        except Exception:
            pass
        emit_activity(
            thread_id=thread_id,
            agent="lead",
            title="Lead 已压缩上下文",
            content=f"已选择 {len(context_pack.relevant_files)} 个相关文件、{len(context_pack.file_outlines)} 个文件大纲和最近变更。",
            workspace_dir=workspace_dir,
            payload={
                "phase": "context_pack",
                "relevant_files": context_pack.relevant_files,
                "file_outline_count": len(context_pack.file_outlines),
            },
        )
    except Exception as exc:
        emit_event(
            thread_id=thread_id,
            event_type="context_pack_failed",
            title="上下文包构建失败",
            content=str(exc),
            agent="system",
            payload={"error": str(exc)},
            workspace_dir=workspace_dir,
        )
    return system


async def inject_parallel_briefing(
    *,
    thread_id: str,
    workspace_dir: str,
    messages: list[dict[str, Any]],
    execution_plan: dict[str, Any],
    uses_runtime_turn_loop: bool,
    briefing_runner: ParallelBriefingRunner,
    subagent_runner: Callable[..., Awaitable[str]],
    emit_event: EmitEvent,
    emit_activity: EmitActivity,
    readonly_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run optional parallel read-only briefing and inject it into Lead messages."""
    if uses_runtime_turn_loop:
        return {}
    emit_activity(
        thread_id=thread_id,
        agent="lead",
        title="Lead 正在创建临时只读分析",
        content="复杂任务会先让临时子 Agent 做只读预分析，完成后自动合并并归档。",
        workspace_dir=workspace_dir,
        payload={"phase": "parallel_briefing"},
    )
    result = await briefing_runner(
        thread_id=thread_id,
        prompt=str(messages[-1].get("content", "")) if messages else "",
        workspace_dir=workspace_dir,
        execution_plan=execution_plan,
        runner=subagent_runner,
        emit_event=emit_event,
        tools=readonly_tools,
    )
    briefing = result.get("briefing") if isinstance(result, dict) else ""
    merge_guidance = result.get("merge_guidance") if isinstance(result, dict) else ""
    parallel_context = "\n\n".join(str(item) for item in [briefing, merge_guidance] if item)
    if not parallel_context:
        return result if isinstance(result, dict) else {}

    messages.append({"role": "user", "content": parallel_context})
    contributions = result.get("contributions") if isinstance(result.get("contributions"), dict) else {}
    emit_event(
        thread_id=thread_id,
        event_type="parallel_briefing_injected",
        title="并行预分析已注入 Lead 上下文",
        content="Lead 将结合临时子 Agent 的只读分析和合并策略继续执行主流程。",
        agent="lead",
        payload={
            "contribution_count": len(contributions.get("contributions", [])),
            "has_merge_guidance": bool(merge_guidance),
        },
        workspace_dir=workspace_dir,
    )
    emit_activity(
        thread_id=thread_id,
        agent="lead",
        title="Lead 已合并临时 Agent 预分析",
        content="只读预分析已经合并进主上下文，接下来进入主 Agent 循环。",
        workspace_dir=workspace_dir,
        payload={
            "phase": "parallel_briefing_merged",
            "has_merge_guidance": bool(merge_guidance),
        },
    )
    return result
