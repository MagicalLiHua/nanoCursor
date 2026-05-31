"""Parallel ephemeral-agent runtime.

This service runs bounded, read-only sub-agent briefings before the main Lead
loop. The goal is to make multi-agent work observable and genuinely parallel
without introducing write conflicts between autonomous workers.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from src.api.services.ephemeral_agent_service import (
    archive_ephemeral_agent,
    complete_ephemeral_agent,
    spawn_ephemeral_agent,
    suggest_ephemeral_agents,
    summarize_ephemeral_agent_contributions,
    update_ephemeral_agent_status,
)
from src.api.services.event_store import get_event_store


Runner = Callable[..., Awaitable[str]]
Emitter = Callable[..., Any]

DEFAULT_PARALLEL_LIMIT = 3


def should_run_parallel_briefing(execution_plan: dict[str, Any] | None) -> bool:
    """Return whether a run should launch parallel read-only workers."""
    if not isinstance(execution_plan, dict) or not execution_plan:
        return False
    if execution_plan.get("strategy") == "lead_direct_reply":
        return False
    stages = execution_plan.get("stages")
    if not isinstance(stages, list) or len(stages) <= 1:
        return False
    return True


def render_parallel_briefing(contributions: dict[str, Any] | None) -> str:
    """Render completed parallel-agent contributions as compact Lead context."""
    if not contributions:
        return ""
    items = contributions.get("contributions") if isinstance(contributions.get("contributions"), list) else []
    if not items:
        return ""

    lines = [
        "【并行子 Agent 预分析】",
        "以下结果来自本轮主循环前并发执行的临时子 Agent。它们只做只读调研/复核，不直接修改文件；请在后续计划、实现和最终回复中吸收这些证据。",
    ]
    for item in items[:DEFAULT_PARALLEL_LIMIT]:
        lines.append(
            f"- {item.get('name') or item.get('role')}: {item.get('summary') or '未提供摘要'} "
            f"(evidence={item.get('evidence_count', 0)}, risks={item.get('risk_count', 0)})"
        )

    risks = contributions.get("risks") if isinstance(contributions.get("risks"), list) else []
    if risks:
        lines.append("并行发现的风险:")
        for risk in risks[:5]:
            detail = risk.get("description") or risk.get("detail") or risk.get("title") or risk
            lines.append(f"- {detail}")

    next_actions = contributions.get("next_actions") if isinstance(contributions.get("next_actions"), list) else []
    if next_actions:
        lines.append("建议后续动作:")
        for action in next_actions[:5]:
            lines.append(f"- {action}")

    return "\n".join(lines)


def load_parallel_proposals(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """Load the persisted parallel proposal artifact for a run."""
    path = _proposal_path(thread_id, workspace_dir)
    if not path.exists():
        return {
            "thread_id": thread_id,
            "workspace_dir": str(Path(workspace_dir).resolve()),
            "proposals": [],
            "summary": {"proposal_count": 0, "risk_count": 0, "suggested_file_count": 0},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "thread_id": thread_id,
            "workspace_dir": str(Path(workspace_dir).resolve()),
            "proposals": [],
            "summary": {"proposal_count": 0, "risk_count": 0, "suggested_file_count": 0},
            "error": "parallel_proposals.json is unreadable",
        }
    if not isinstance(data, dict):
        data = {}
    data.setdefault("thread_id", thread_id)
    data.setdefault("workspace_dir", str(Path(workspace_dir).resolve()))
    data.setdefault("proposals", [])
    data.setdefault("summary", {"proposal_count": len(data["proposals"]), "risk_count": 0, "suggested_file_count": 0})
    return data


def save_parallel_proposals(thread_id: str, workspace_dir: str, proposals: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist child-agent proposal artifacts atomically."""
    unique_files = sorted({
        str(path)
        for proposal in proposals
        for path in proposal.get("suggested_files", [])
        if str(path).strip()
    })
    risk_count = sum(len(proposal.get("risks", [])) for proposal in proposals)
    artifact = {
        "thread_id": thread_id,
        "workspace_dir": str(Path(workspace_dir).resolve()),
        "generated_at": time.time(),
        "proposals": proposals,
        "summary": {
            "proposal_count": len(proposals),
            "risk_count": risk_count,
            "suggested_file_count": len(unique_files),
            "suggested_files": unique_files[:20],
        },
    }
    path = _proposal_path(thread_id, workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return artifact


def load_parallel_merge_plan(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """Load the persisted Lead merge plan for parallel proposals."""
    path = _merge_plan_path(thread_id, workspace_dir)
    if not path.exists():
        return _empty_merge_plan(thread_id, workspace_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = _empty_merge_plan(thread_id, workspace_dir)
        data["error"] = "parallel_merge_plan.json is unreadable"
        return data
    if not isinstance(data, dict):
        return _empty_merge_plan(thread_id, workspace_dir)
    data.setdefault("thread_id", thread_id)
    data.setdefault("workspace_dir", str(Path(workspace_dir).resolve()))
    data.setdefault("summary", {})
    return data


def _detect_proposal_conflicts(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect file-level conflicts between proposals.

    Returns a list of conflict dicts, each with 'file' and 'agents' keys,
    for files that appear in multiple proposals' suggested_files.
    """
    file_to_agents: dict[str, list[str]] = {}
    for proposal in proposals:
        agent_name = proposal.get("name") or proposal.get("role") or "unknown"
        for path in proposal.get("suggested_files", []):
            if path:
                file_to_agents.setdefault(str(path), []).append(agent_name)
    return [
        {"file": file, "agents": agents}
        for file, agents in file_to_agents.items()
        if len(agents) > 1
    ]


def build_parallel_merge_plan(
    thread_id: str,
    workspace_dir: str,
    execution_plan: dict[str, Any] | None = None,
    proposal_artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a deterministic Lead merge plan from parallel child-agent proposals."""
    artifact = proposal_artifact or load_parallel_proposals(thread_id, workspace_dir)
    proposals = artifact.get("proposals") if isinstance(artifact.get("proposals"), list) else []
    if not proposals:
        plan = _empty_merge_plan(thread_id, workspace_dir)
        _write_json_atomic(_merge_plan_path(thread_id, workspace_dir), plan)
        return plan

    accepted: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        status = str(proposal.get("status") or "").lower()
        if status in {"completed", "archived", "ready"} or proposal.get("summary"):
            accepted.append(proposal)
        else:
            deferred.append({**proposal, "defer_reason": "子 Agent 未产生可用摘要。"})

    suggested_files = _unique([
        str(path)
        for proposal in accepted
        for path in proposal.get("suggested_files", [])
        if str(path).strip()
    ])
    risks = [
        risk
        for proposal in accepted
        for risk in proposal.get("risks", [])
        if isinstance(risk, dict)
    ]
    next_actions = _unique([
        str(action)
        for proposal in accepted
        for action in proposal.get("recommended_next_actions", [])
        if str(action).strip()
    ])

    # B5: Detect file-level conflicts between proposals
    conflicts = _detect_proposal_conflicts(accepted)
    if conflicts:
        for conflict in conflicts:
            risks.append({
                "description": f"文件冲突: {conflict['file']} 被多个 Agent 关注: {', '.join(conflict['agents'])}",
                "level": "medium",
                "type": "file_conflict",
            })

    plan = {
        "thread_id": thread_id,
        "workspace_dir": str(Path(workspace_dir).resolve()),
        "generated_at": time.time(),
        "status": "ready" if accepted else "empty",
        "merge_mode": "lead_supervised",
        "accepted_proposals": [_compact_proposal(proposal) for proposal in accepted],
        "deferred_proposals": [_compact_proposal(proposal) for proposal in deferred],
        "suggested_files": suggested_files[:30],
        "risks": risks[:20],
        "recommended_next_actions": next_actions[:20],
        "stage_guidance": _stage_guidance(accepted, execution_plan or {}),
        "file_conflicts": conflicts[:10],
        "summary": {
            "proposal_count": len(proposals),
            "accepted_count": len(accepted),
            "deferred_count": len(deferred),
            "risk_count": len(risks),
            "suggested_file_count": len(suggested_files),
            "next_action_count": len(next_actions),
            "file_conflict_count": len(conflicts),
        },
    }
    _write_json_atomic(_merge_plan_path(thread_id, workspace_dir), plan)
    return plan


def render_parallel_merge_guidance(merge_plan: dict[str, Any] | None) -> str:
    """Render merge plan as compact instructions for the Lead agent."""
    if not merge_plan or merge_plan.get("status") != "ready":
        return ""
    summary = merge_plan.get("summary") if isinstance(merge_plan.get("summary"), dict) else {}
    lines = [
        "【Lead 合并策略】",
        (
            f"已接受 {summary.get('accepted_count', 0)} 个并行子 Agent 提案，"
            f"暂缓 {summary.get('deferred_count', 0)} 个。请把它们作为执行参考，而不是直接照抄。"
        ),
    ]
    files = merge_plan.get("suggested_files") if isinstance(merge_plan.get("suggested_files"), list) else []
    if files:
        lines.append("优先关注文件:")
        for path in files[:8]:
            lines.append(f"- {path}")

    stage_guidance = merge_plan.get("stage_guidance") if isinstance(merge_plan.get("stage_guidance"), list) else []
    if stage_guidance:
        lines.append("阶段吸收方式:")
        for item in stage_guidance[:6]:
            notes = "; ".join(str(note) for note in item.get("notes", [])[:3])
            lines.append(f"- {item.get('stage_id')}: {notes}")

    file_conflicts = merge_plan.get("file_conflicts") if isinstance(merge_plan.get("file_conflicts"), list) else []
    if file_conflicts:
        lines.append("文件冲突（多个 Agent 关注同一文件）:")
        for conflict in file_conflicts[:5]:
            lines.append(f"- {conflict.get('file')}: {', '.join(conflict.get('agents', []))}")

    risks = merge_plan.get("risks") if isinstance(merge_plan.get("risks"), list) else []
    if risks:
        lines.append("必须复核的并行风险:")
        for risk in risks[:5]:
            lines.append(f"- {risk.get('description') or risk.get('detail') or risk.get('title') or risk}")
    return "\n".join(lines)


async def run_parallel_agent_briefing(
    *,
    thread_id: str,
    prompt: str,
    workspace_dir: str,
    execution_plan: dict[str, Any],
    runner: Runner,
    emit_event: Emitter,
    tools: list[dict[str, Any]] | None = None,
    max_agents: int = DEFAULT_PARALLEL_LIMIT,
) -> dict[str, Any]:
    """Run bounded ephemeral workers concurrently and archive their results."""
    if not should_run_parallel_briefing(execution_plan):
        return {"enabled": False, "reason": "not_applicable", "contributions": []}

    suggestions = suggest_ephemeral_agents(
        prompt,
        mcp_plan=execution_plan.get("mcp_plan") if isinstance(execution_plan.get("mcp_plan"), list) else [],
        workspace_dir=workspace_dir,
        max_agents=max_agents,
    ).get("suggestions", [])
    specs = suggestions[:max(1, min(max_agents, DEFAULT_PARALLEL_LIMIT))]
    if not specs:
        return {"enabled": False, "reason": "no_suggestions", "contributions": []}

    agents = []
    for spec in specs:
        agent = spawn_ephemeral_agent(thread_id, spec, workspace_dir)
        agents.append(agent)

    emit_event(
        thread_id=thread_id,
        event_type="parallel_agents_started",
        title="并行子 Agent 已启动",
        content=f"Lead 已派生 {len(agents)} 个临时子 Agent 做只读预分析。",
        agent="lead",
        payload={"agents": agents, "parallel_limit": len(agents), "mode": "read_only_briefing"},
        workspace_dir=workspace_dir,
    )

    semaphore = asyncio.Semaphore(max(1, min(len(agents), DEFAULT_PARALLEL_LIMIT)))

    async def _run_one(agent: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            result = await run_single_ephemeral_agent(
                thread_id=thread_id,
                prompt=prompt,
                workspace_dir=workspace_dir,
                execution_plan=execution_plan,
                agent=agent,
                runner=runner,
                emit_event=emit_event,
                tools=tools,
                mode="parallel",
            )
            if result.get("ok"):
                return {"agent_id": agent["agent_id"], "ok": True, "summary": result.get("summary", "")}
            return {"agent_id": agent["agent_id"], "ok": False, "error": result.get("error", "")}

    results = await asyncio.gather(*[_run_one(agent) for agent in agents])
    contributions = summarize_ephemeral_agent_contributions(thread_id, workspace_dir)
    proposals = _proposals_from_contributions(contributions)
    proposal_artifact = save_parallel_proposals(thread_id, workspace_dir, proposals)
    merge_plan = build_parallel_merge_plan(thread_id, workspace_dir, execution_plan, proposal_artifact)
    emit_event(
        thread_id=thread_id,
        event_type="parallel_agents_completed",
        title="并行子 Agent 已汇总",
        content=f"{contributions['summary']['completed_count']} 个子 Agent 完成预分析。",
        agent="lead",
        payload={
            "results": results,
            "contributions": contributions,
            "proposal_artifact": proposal_artifact,
            "merge_plan": merge_plan,
        },
        workspace_dir=workspace_dir,
    )
    emit_event(
        thread_id=thread_id,
        event_type="parallel_proposals_reviewed",
        title="Lead 合并策略已生成",
        content=f"接受 {merge_plan['summary']['accepted_count']} 个并行提案，暂缓 {merge_plan['summary']['deferred_count']} 个。",
        agent="lead",
        payload={"merge_plan": merge_plan},
        workspace_dir=workspace_dir,
    )
    return {
        "enabled": True,
        "results": results,
        "contributions": contributions,
        "proposal_artifact": proposal_artifact,
        "merge_plan": merge_plan,
        "merge_guidance": render_parallel_merge_guidance(merge_plan),
        "briefing": render_parallel_briefing(contributions),
    }


async def run_single_ephemeral_agent(
    *,
    thread_id: str,
    prompt: str,
    workspace_dir: str,
    execution_plan: dict[str, Any] | None,
    agent: dict[str, Any],
    runner: Runner,
    emit_event: Emitter,
    tools: list[dict[str, Any]] | None = None,
    mode: str = "runtime_spawn",
    change_context: str = "",
) -> dict[str, Any]:
    """Run one temporary Agent as a bounded proposal worker and archive it."""
    started_at = time.time()
    progress_type = "parallel_agent_progress" if mode == "parallel" else "agent_run_started"
    result_type = "parallel_agent_result" if mode == "parallel" else "agent_result_merged"
    failed_type = "parallel_agent_failed" if mode == "parallel" else "agent_run_failed"
    action_text = "开始并行预分析。" if mode == "parallel" else "开始执行临时 Agent 任务。"

    update_ephemeral_agent_status(thread_id, agent["agent_id"], "working", workspace_dir, action_text)
    emit_event(
        thread_id=thread_id,
        event_type=progress_type,
        title=f"{agent.get('name')}: 开始执行",
        content=agent.get("goal", ""),
        agent=agent.get("name") or "Ephemeral Agent",
        payload={"agent_id": agent.get("agent_id"), "status": "working", "mode": mode},
        workspace_dir=workspace_dir,
    )
    try:
        output = await runner(
            _worker_prompt(prompt, agent, execution_plan or {}, change_context=change_context),
            system=_worker_system(agent, workspace_dir),
            agent_type=agent.get("role") or agent.get("name") or "Worker",
            tools=tools,
        )
        if str(output or "").lstrip().startswith("Error:"):
            raise RuntimeError(str(output).removeprefix("Error:").strip() or "子 Agent 返回错误。")
        result = _normalise_worker_result(agent, output, started_at)
        completed = complete_ephemeral_agent(thread_id, agent["agent_id"], result, workspace_dir)
        emit_event(
            thread_id=thread_id,
            event_type=result_type,
            title=f"{agent.get('name')}: 结果已合并",
            content=result["summary"],
            agent=agent.get("name") or "Ephemeral Agent",
            payload={
                "agent_id": agent.get("agent_id"),
                "duration_ms": result["duration_ms"],
                "mode": mode,
                "result": completed.get("result", result),
                "agent": completed,
            },
            workspace_dir=workspace_dir,
        )
        return {
            "ok": True,
            "agent": completed,
            "result": completed.get("result", result),
            "summary": result["summary"],
        }
    except Exception as exc:  # Keep the main run alive if a worker fails.
        archived = archive_ephemeral_agent(thread_id, agent["agent_id"], f"临时 Agent 执行失败: {exc}", workspace_dir)
        emit_event(
            thread_id=thread_id,
            event_type=failed_type,
            title=f"{agent.get('name')}: 执行失败",
            content=str(exc),
            agent=agent.get("name") or "Ephemeral Agent",
            payload={"agent_id": agent.get("agent_id"), "error": str(exc), "mode": mode, "agent": archived},
            workspace_dir=workspace_dir,
        )
        return {"ok": False, "agent": archived, "error": str(exc)}


def _proposal_path(thread_id: str, workspace_dir: str) -> Path:
    return get_event_store().run_dir(thread_id, workspace_dir) / "parallel_proposals.json"


def _merge_plan_path(thread_id: str, workspace_dir: str) -> Path:
    return get_event_store().run_dir(thread_id, workspace_dir) / "parallel_merge_plan.json"


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _empty_merge_plan(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "workspace_dir": str(Path(workspace_dir).resolve()),
        "generated_at": time.time(),
        "status": "empty",
        "merge_mode": "lead_supervised",
        "accepted_proposals": [],
        "deferred_proposals": [],
        "suggested_files": [],
        "risks": [],
        "recommended_next_actions": [],
        "stage_guidance": [],
        "summary": {
            "proposal_count": 0,
            "accepted_count": 0,
            "deferred_count": 0,
            "risk_count": 0,
            "suggested_file_count": 0,
            "next_action_count": 0,
        },
    }


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _compact_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_id": proposal.get("agent_id", ""),
        "name": proposal.get("name", ""),
        "role": proposal.get("role", ""),
        "summary": proposal.get("summary", ""),
        "suggested_files": proposal.get("suggested_files", []),
        "risk_count": len(proposal.get("risks", []) if isinstance(proposal.get("risks"), list) else []),
        "recommended_next_actions": proposal.get("recommended_next_actions", []),
    }


def _stage_guidance(proposals: list[dict[str, Any]], execution_plan: dict[str, Any]) -> list[dict[str, Any]]:
    stages = execution_plan.get("stages") if isinstance(execution_plan.get("stages"), list) else []
    stage_ids = {str(stage.get("id")) for stage in stages if isinstance(stage, dict) and stage.get("id")}
    guidance: dict[str, list[str]] = {}

    def add(stage_id: str, note: str) -> None:
        if stage_ids and stage_id not in stage_ids:
            return
        guidance.setdefault(stage_id, [])
        if note not in guidance[stage_id]:
            guidance[stage_id].append(note)

    for proposal in proposals:
        role = str(proposal.get("role") or proposal.get("name") or "").lower()
        files = [str(path) for path in proposal.get("suggested_files", [])[:5]]
        file_note = f"吸收 {proposal.get('name') or role} 的建议；关注 {', '.join(files)}。" if files else f"吸收 {proposal.get('name') or role} 的分析摘要。"
        if "frontend" in role or "design" in role:
            add("design_review", file_note)
            add("implement", file_note)
        elif "backend" in role or "implementation" in role:
            add("implement", file_note)
        elif "test" in role or "review" in role:
            add("verify", file_note)
            add("diff_review", file_note)
        elif "docs" in role:
            add("verify", file_note)
        else:
            add("plan", file_note)

    return [{"stage_id": stage_id, "notes": notes[:5]} for stage_id, notes in guidance.items()]


def _proposals_from_contributions(contributions: dict[str, Any]) -> list[dict[str, Any]]:
    items = contributions.get("contributions") if isinstance(contributions.get("contributions"), list) else []
    risks = contributions.get("risks") if isinstance(contributions.get("risks"), list) else []
    next_actions = contributions.get("next_actions") if isinstance(contributions.get("next_actions"), list) else []
    proposals: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "")
        artifacts = item.get("artifacts") if isinstance(item.get("artifacts"), list) else []
        artifact_files = [
            str(artifact.get("path"))
            for artifact in artifacts
            if isinstance(artifact, dict) and artifact.get("path")
        ]
        proposal_risks = [
            risk
            for risk in risks
            if isinstance(risk, dict) and risk.get("agent_id") == item.get("agent_id")
        ]
        proposals.append({
            "agent_id": item.get("agent_id", ""),
            "name": item.get("name", ""),
            "role": item.get("role", ""),
            "status": item.get("terminal_status") or item.get("status", ""),
            "summary": summary,
            "suggested_files": _unique([*artifact_files, *_extract_file_mentions(summary)]),
            "risks": proposal_risks,
            "recommended_next_actions": item.get("recommended_next_actions") or next_actions[:3],
            "evidence_count": item.get("evidence_count", 0),
            "artifact_count": item.get("artifact_count", 0),
        })
    return proposals


def _worker_system(agent: dict[str, Any], workspace_dir: str) -> str:
    return (
        f"你是 nanoCursor 的临时子 Agent：{agent.get('name')} ({agent.get('role')})。\n"
        f"工作区: {workspace_dir}\n"
        "你只能做只读分析、搜索、阅读和风险判断；不要写文件、不要修改代码、不要执行会改变项目状态的命令。\n"
        "输出要短而结构化，包含 Summary、Evidence、Risks、Recommended Next Actions。"
    )


def _worker_prompt(
    prompt: str,
    agent: dict[str, Any],
    execution_plan: dict[str, Any],
    change_context: str = "",
) -> str:
    scope = agent.get("task_scope") if isinstance(agent.get("task_scope"), dict) else {}
    include = ", ".join(str(item) for item in scope.get("include", [])[:6]) or "."
    stages = execution_plan.get("stages") if isinstance(execution_plan.get("stages"), list) else []
    stage_text = "; ".join(f"{stage.get('id')}:{stage.get('title')}" for stage in stages[:8] if isinstance(stage, dict))
    parts = [
        f"用户任务：{prompt}\n",
        f"你的目标：{agent.get('goal')}\n",
        f"建议关注范围：{include}\n",
        f"本轮执行阶段：{stage_text}\n",
    ]
    if change_context:
        parts.append(f"{change_context}\n")
    parts.append(
        "请完成你的专项分析。不要改文件。请输出：\n"
        "## Summary\n- ...\n## Evidence\n- ...\n## Risks\n- ...\n## Recommended Next Actions\n- ..."
    )
    return "\n".join(parts)


def _normalise_worker_result(agent: dict[str, Any], output: str, started_at: float) -> dict[str, Any]:
    summary = _first_meaningful_line(output) or f"{agent.get('name')} 已完成预分析。"
    suggested_files = _extract_file_mentions(output)
    return {
        "summary": summary[:800],
        "evidence": [{"type": "parallel_briefing", "content": output[:4000]}],
        "risks": _extract_risk_items(output),
        "artifacts": [{"type": "suggested_file", "path": path} for path in suggested_files],
        "recommended_next_actions": _extract_next_actions(output),
        "duration_ms": int((time.time() - started_at) * 1000),
    }


def _first_meaningful_line(text: str) -> str:
    for line in str(text or "").splitlines():
        clean = line.strip().lstrip("-*# ").strip()
        if clean and clean.lower() not in {"summary", "evidence", "risks", "recommended next actions"}:
            return clean
    return ""


def _extract_risk_items(text: str) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    in_risks = False
    for line in str(text or "").splitlines():
        clean = line.strip()
        lower = clean.lower()
        if lower.startswith("## risks") or clean.startswith("风险"):
            in_risks = True
            continue
        if in_risks and clean.startswith("## "):
            break
        if in_risks and clean.lstrip("-* ").strip():
            item = clean.lstrip("-* ").strip()
            if item.lower() not in {"none", "无"}:
                risks.append({"description": item[:500], "level": "medium"})
    return risks[:5]


def _extract_next_actions(text: str) -> list[str]:
    actions: list[str] = []
    in_actions = False
    for line in str(text or "").splitlines():
        clean = line.strip()
        lower = clean.lower()
        if lower.startswith("## recommended next actions") or clean.startswith("后续"):
            in_actions = True
            continue
        if in_actions and clean.startswith("## "):
            break
        item = clean.lstrip("-* ").strip()
        if in_actions and item:
            actions.append(item[:300])
    return actions[:5]


def _extract_file_mentions(text: str) -> list[str]:
    """Extract likely repo-relative file paths from model text."""
    candidates = re.findall(
        r"(?<![\w/.-])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,8}|(?<![\w.-])[\w.-]+\.(?:py|js|ts|tsx|jsx|css|md|json|toml|yaml|yml|html)",
        str(text or ""),
    )
    blocked_prefixes = {"http", "https"}
    result: list[str] = []
    for candidate in candidates:
        clean = candidate.strip("`'\"，,。.;:()[]{}")
        if not clean or clean.split("/", 1)[0].lower() in blocked_prefixes:
            continue
        if clean not in result:
            result.append(clean)
    return result[:12]
