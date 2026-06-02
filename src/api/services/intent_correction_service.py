"""Runtime intent route correction for persisted runs."""

from __future__ import annotations

import time
import uuid
from typing import Any

from src.api.models import IntentDecision
from src.api.services.event_store import get_event_store
from src.api.services.intent_guards import IntentGuardResult, evaluate_intent_guards
from src.api.services.intent_normalizer import normalize_intent_decision
from src.api.services.intent_router import classify_user_intent


def correct_run_intent(
    thread_id: str,
    workspace_dir: str,
    *,
    route: str,
    reason: str,
    complexity: str | None = None,
    evidence: dict[str, Any] | None = None,
    source: str = "runtime_correction",
) -> dict[str, Any]:
    """Correct a run's normalized intent decision and persist the audit trail."""
    store = get_event_store()
    session = store.get_session(thread_id, workspace_dir)
    if not session:
        raise ValueError(f"Run {thread_id} not found")

    old_intent = _load_intent(session)
    prompt = str(session.get("prompt") or "")
    guard_result = evaluate_intent_guards(prompt, old_intent)
    if guard_result.risk_level != "high":
        guard_result = IntentGuardResult(
            hits=guard_result.hits,
            hard_decision=None,
            risk_level=guard_result.risk_level,
            risk_reasons=guard_result.risk_reasons,
        )

    raw = old_intent.model_dump()
    raw.update(
        {
            "route": route,
            "complexity": complexity or _complexity_for_route(route),
            "level": complexity or _complexity_for_route(route),
            "rationale": reason,
            "source": source,
            "confidence": max(float(old_intent.confidence or 0), 0.7),
            "runtime_correction": {
                "reason": reason,
                "evidence": evidence or {},
                "source": source,
            },
        }
    )
    new_intent = normalize_intent_decision(raw, fallback=old_intent, guards=guard_result)
    correction = {
        "id": f"intent-correction-{uuid.uuid4().hex[:10]}",
        "thread_id": thread_id,
        "old_route": old_intent.route,
        "new_route": new_intent.route,
        "old_complexity": old_intent.complexity,
        "new_complexity": new_intent.complexity,
        "old_execution_route": old_intent.execution_route,
        "new_execution_route": new_intent.execution_route,
        "reason": reason,
        "evidence": evidence or {},
        "source": source,
        "created_at": time.time(),
        "guard_hits": new_intent.guard_hits,
    }
    corrections = list(session.get("intent_corrections") or [])
    corrections.append(correction)

    execution_plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
    if execution_plan:
        execution_plan["intent_decision"] = new_intent.model_dump()
        execution_plan.setdefault("summary", {})["intent_route"] = new_intent.route
        execution_plan["complexity"] = {
            **dict(execution_plan.get("complexity") or {}),
            "level": new_intent.level,
            "route": new_intent.route,
            "execution_route": new_intent.execution_route,
            "intent": new_intent.intent,
            "requires_workspace_write": new_intent.requires_workspace_write,
            "requires_workspace_read": new_intent.requires_workspace_read,
            "requires_shell": new_intent.requires_shell,
            "requires_approval": new_intent.requires_approval,
            "requires_execution": new_intent.requires_execution,
            "intent_decision": new_intent.model_dump(),
        }

    store.update_session(
        thread_id,
        workspace_dir,
        intent_decision=new_intent.model_dump(),
        intent_decision_normalized=new_intent.model_dump(),
        intent_corrections=corrections,
        execution_plan=execution_plan,
    )
    event = store.append_event(
        thread_id,
        "intent_route_corrected",
        title="运行意图已纠偏",
        content=f"{old_intent.route} -> {new_intent.route}: {reason}",
        agent="lead",
        payload={"correction": correction, "intent_decision": new_intent.model_dump()},
        workspace_dir=workspace_dir,
    )
    _update_loop_state(thread_id, workspace_dir, session, new_intent, reason, event.id)
    return {
        "thread_id": thread_id,
        "correction": correction,
        "intent_decision": new_intent.model_dump(),
        "event_id": event.id,
    }


def _load_intent(session: dict[str, Any]) -> IntentDecision:
    raw = session.get("intent_decision_normalized")
    if not isinstance(raw, dict):
        raw = session.get("intent_decision")
    if isinstance(raw, dict):
        return IntentDecision.model_validate(raw)
    return IntentDecision.model_validate(classify_user_intent(str(session.get("prompt") or "")))


def _complexity_for_route(route: str) -> str:
    if route in {"direct_answer", "read_only", "review_only", "clarification_needed"}:
        return "simple"
    if route in {"small_edit", "test_only"}:
        return "small_code"
    if route == "risky_operation":
        return "high_risk"
    return "medium"


def _update_loop_state(
    thread_id: str,
    workspace_dir: str,
    session: dict[str, Any],
    intent: IntentDecision,
    reason: str,
    event_id: str,
) -> None:
    try:
        from src.api.services.agent_loop_state_service import (
            append_loop_step,
            init_agent_loop_state,
            load_agent_loop_state,
        )

        existing = load_agent_loop_state(thread_id, workspace_dir)
        init_agent_loop_state(
            thread_id,
            workspace_dir,
            user_request=str(session.get("prompt") or ""),
            intent=intent,
            conversation_id=session.get("conversation_id"),
            max_steps=existing.max_steps if existing else 20,
        )
        append_loop_step(
            thread_id,
            workspace_dir,
            phase="decide",
            action={
                "type": "summarize",
                "goal": "Correct the run intent route based on runtime evidence.",
                "agent": "Lead",
                "final_message": reason,
            },
            summary=reason,
            event_id=event_id,
        )
    except Exception:
        return
