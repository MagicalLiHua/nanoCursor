"""Child-agent context and merge evidence utilities."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from src.api.services.agent_loop_state_service import append_loop_step
from src.api.services.event_store import get_event_store
from src.api.services.run_state_service import build_run_context_pack


def build_child_agent_context_pack(
    *,
    thread_id: str,
    workspace_dir: str,
    prompt: str,
    execution_plan: dict[str, Any] | None,
    agent: dict[str, Any],
    change_context: str = "",
) -> dict[str, Any]:
    """Build a scoped ContextPack for one temporary child Agent."""
    agent_id = str(agent.get("agent_id") or "")
    task_scope = agent.get("task_scope") if isinstance(agent.get("task_scope"), dict) else {}
    turn_context = {
        "step": f"agent:{agent_id}",
        "agent": _agent_identity(agent),
        "agent_id": agent_id,
        "agent_name": str(agent.get("name") or ""),
        "agent_role": str(agent.get("role") or ""),
        "parent_agent": str(agent.get("parent_agent") or "Lead"),
        "task_scope": task_scope,
        "allowed_actions": task_scope.get("allowed_actions") if isinstance(task_scope, dict) else [],
        "change_context": change_context[:4000],
        "is_child_agent_context": True,
        "context_isolation": {
            "mode": "child_agent_scoped",
            "lead_receives": "summary_and_evidence_only",
            "write_policy": "read_only_by_default",
        },
        "execution_plan_summary": _execution_plan_summary(execution_plan or {}),
        "user_request": prompt[:4000],
    }
    pack = build_run_context_pack(
        thread_id,
        workspace_dir,
        purpose="child_agent",
        task_id=agent_id or None,
        turn_context=turn_context,
    )
    get_event_store().append_event(
        thread_id,
        "agent_context_pack_built",
        title=f"{agent.get('name') or 'Agent'} 独立上下文已构建",
        content=f"Selected {len(pack.get('selected_files', []))} files for child Agent.",
        agent=str(agent.get("name") or "Ephemeral Agent"),
        payload={
            "agent_id": agent_id,
            "context_pack_id": pack.get("id"),
            "selected_file_count": len(pack.get("selected_files", [])),
            "purpose": pack.get("purpose"),
            "task_scope": task_scope,
        },
        workspace_dir=workspace_dir,
    )
    return pack


def build_agent_evidence_pack(
    *,
    thread_id: str,
    workspace_dir: str,
    agent: dict[str, Any],
    result: dict[str, Any],
    context_pack: dict[str, Any] | None = None,
    mode: str = "runtime_spawn",
) -> dict[str, Any]:
    """Persist the compressed result Lead should consume from one child Agent."""
    context_pack = context_pack or {}
    selected_files = [
        str(item.get("path") or item.get("file") or "")
        for item in context_pack.get("selected_files", [])
        if isinstance(item, dict) and (item.get("path") or item.get("file"))
    ]
    evidence_pack = {
        "id": f"agent-evidence-{uuid.uuid4().hex[:12]}",
        "thread_id": thread_id,
        "workspace_dir": str(Path(workspace_dir).resolve()),
        "agent": _agent_identity(agent),
        "agent_id": str(agent.get("agent_id") or ""),
        "mode": mode,
        "context_pack_id": str(context_pack.get("id") or ""),
        "context_pack_purpose": str(context_pack.get("purpose") or ""),
        "selected_files": selected_files[:20],
        "selected_file_count": len(selected_files),
        "summary": str(result.get("summary") or "")[:1200],
        "evidence": _list_of_dicts(result.get("evidence"))[:12],
        "risks": _list_of_dicts(result.get("risks"))[:12],
        "artifacts": _list_of_dicts(result.get("artifacts"))[:20],
        "recommended_next_actions": [str(item)[:400] for item in result.get("recommended_next_actions", []) if item][:12]
        if isinstance(result.get("recommended_next_actions"), list) else [],
        "duration_ms": int(result.get("duration_ms") or 0),
        "created_at": time.time(),
    }
    path = _evidence_pack_path(thread_id, workspace_dir, evidence_pack["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, evidence_pack)
    get_event_store().append_event(
        thread_id,
        "agent_evidence_pack_built",
        title=f"{agent.get('name') or 'Agent'} 证据包已生成",
        content=evidence_pack["summary"],
        agent=str(agent.get("name") or "Ephemeral Agent"),
        payload={
            "agent_id": evidence_pack["agent_id"],
            "evidence_pack_id": evidence_pack["id"],
            "context_pack_id": evidence_pack["context_pack_id"],
            "selected_file_count": evidence_pack["selected_file_count"],
            "risk_count": len(evidence_pack["risks"]),
            "artifact_count": len(evidence_pack["artifacts"]),
        },
        workspace_dir=workspace_dir,
    )
    return evidence_pack


def record_agent_result_merge(
    *,
    thread_id: str,
    workspace_dir: str,
    agent: dict[str, Any],
    evidence_pack: dict[str, Any],
    mode: str = "runtime_spawn",
) -> dict[str, Any]:
    """Record the Lead's merge decision for a child Agent result."""
    action = {
        "type": "merge_agent_result",
        "goal": f"Merge child Agent result from {agent.get('name') or agent.get('role') or 'Agent'}.",
        "agent": "Lead",
        "context_requirements": {
            "agent_id": str(agent.get("agent_id") or ""),
            "agent_name": str(agent.get("name") or ""),
            "agent_role": str(agent.get("role") or ""),
            "evidence_pack_id": str(evidence_pack.get("id") or ""),
            "context_pack_id": str(evidence_pack.get("context_pack_id") or ""),
            "mode": mode,
            "lead_consumes": "summary_and_evidence_only",
            "summary": str(evidence_pack.get("summary") or "")[:800],
            "risk_count": len(evidence_pack.get("risks", [])) if isinstance(evidence_pack.get("risks"), list) else 0,
            "artifact_count": len(evidence_pack.get("artifacts", [])) if isinstance(evidence_pack.get("artifacts"), list) else 0,
        },
    }
    try:
        state = append_loop_step(
            thread_id,
            workspace_dir,
            action=action,
            phase="synthesize",
            summary=f"Lead merged evidence from {agent.get('name') or agent.get('role') or 'Agent'}.",
            context_pack_id=str(evidence_pack.get("context_pack_id") or "") or None,
        )
        step = state.steps[-1]
        get_event_store().append_event(
            thread_id,
            "agent_result_merge_recorded",
            title=f"Lead 已合并 {agent.get('name') or 'Agent'} 结果",
            content=str(evidence_pack.get("summary") or ""),
            agent="Lead",
            payload={
                "agent_id": str(agent.get("agent_id") or ""),
                "evidence_pack_id": str(evidence_pack.get("id") or ""),
                "loop_step_id": step.id,
                "loop_action_type": "merge_agent_result",
                "mode": mode,
            },
            workspace_dir=workspace_dir,
        )
        return {"recorded": True, "loop_step_id": step.id, "action": action}
    except Exception as exc:
        get_event_store().append_event(
            thread_id,
            "agent_result_merge_record_failed",
            title=f"Lead 合并 {agent.get('name') or 'Agent'} 结果失败",
            content=str(exc),
            agent="Lead",
            payload={
                "agent_id": str(agent.get("agent_id") or ""),
                "evidence_pack_id": str(evidence_pack.get("id") or ""),
                "error": str(exc),
                "action": action,
            },
            workspace_dir=workspace_dir,
        )
        return {"recorded": False, "error": str(exc), "action": action}


def _evidence_pack_path(thread_id: str, workspace_dir: str, pack_id: str) -> Path:
    return get_event_store().run_dir(thread_id, workspace_dir) / "agents" / f"{pack_id}.json"


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _agent_identity(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_id": str(agent.get("agent_id") or ""),
        "name": str(agent.get("name") or ""),
        "role": str(agent.get("role") or ""),
        "parent_agent": str(agent.get("parent_agent") or "Lead"),
        "goal": str(agent.get("goal") or ""),
        "status": str(agent.get("status") or ""),
    }


def _execution_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    return {
        "strategy": str(plan.get("strategy") or ""),
        "stage_count": len(stages),
        "stages": [
            {
                "id": str(stage.get("id") or ""),
                "title": str(stage.get("title") or ""),
                "owner": str(stage.get("owner") or ""),
            }
            for stage in stages[:12]
            if isinstance(stage, dict)
        ],
    }


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]
