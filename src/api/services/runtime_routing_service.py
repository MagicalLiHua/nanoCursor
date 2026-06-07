"""Route lightweight user intents through observable Runtime turns."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from src.api.services.runtime_evidence_service import collect_runtime_delivery_evidence
from src.api.services.runtime_turn_service import run_runtime_turn


StreamTurn = Callable[[list[dict[str, Any]], dict[str, Any] | None], Awaitable[str]]
SyncRunContext = Callable[[str, str], Any]
TurnRunner = Callable[..., Awaitable[Any]]
EvidenceCollector = Callable[..., Any]


LIGHTWEIGHT_RUNTIME_ROUTES = frozenset({"direct_answer", "read_only", "small_edit"})


def uses_lightweight_runtime(intent_route: str) -> bool:
    """Return whether an intent should use the controller-owned lightweight path."""
    return intent_route in LIGHTWEIGHT_RUNTIME_ROUTES


def tools_for_lightweight_route(
    intent_route: str,
    *,
    readonly_tools: list[dict[str, Any]],
    small_edit_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select the bounded tool surface for a lightweight route."""
    if intent_route == "direct_answer":
        return []
    if intent_route == "small_edit":
        return small_edit_tools
    return readonly_tools


def first_action_for_route(intent_route: str) -> dict[str, Any]:
    """Build the first controller action for a lightweight route."""
    if intent_route == "direct_answer":
        action_type = "answer"
        goal = "Answer the user directly without creating tasks."
    elif intent_route == "small_edit":
        action_type = "inspect_project"
        goal = "Inspect the workspace, perform the requested controlled local edit, and gather evidence."
    else:
        action_type = "inspect_project"
        goal = "Inspect the workspace with read-only tools and answer the user's question."
    return {
        "type": action_type,
        "goal": goal,
        "agent": "Lead",
        "context_requirements": {"intent_route": intent_route},
    }


async def execute_lightweight_runtime_route(
    *,
    thread_id: str,
    workspace_dir: str,
    intent_route: str,
    stream_turn: StreamTurn,
    readonly_tools: list[dict[str, Any]],
    small_edit_tools: list[dict[str, Any]],
    tool_evidence: list[dict[str, Any]],
    sync_run_context: SyncRunContext,
    turn_runner: TurnRunner = run_runtime_turn,
    evidence_collector: EvidenceCollector = collect_runtime_delivery_evidence,
) -> str:
    """Execute direct-answer, read-only, or small-edit routes and return the reply."""
    if not uses_lightweight_runtime(intent_route):
        raise ValueError(f"Unsupported lightweight runtime route: {intent_route}")

    toolset = tools_for_lightweight_route(
        intent_route,
        readonly_tools=readonly_tools,
        small_edit_tools=small_edit_tools,
    )

    async def _execute_stream(_action: Any, turn_context: dict[str, Any]) -> dict[str, Any]:
        output = await stream_turn(toolset, turn_context)
        return {
            "executed": True,
            "result": "success",
            "adapter": "agent_loop_stream",
            "output": output,
        }

    first_turn = await turn_runner(
        thread_id,
        workspace_dir,
        action=first_action_for_route(intent_route),
        executor=_execute_stream,
    )
    result = str(first_turn.execution_result.get("output") or "")

    if intent_route == "read_only":
        await turn_runner(
            thread_id,
            workspace_dir,
            action={
                "type": "answer",
                "goal": "Answer from the inspected read-only evidence.",
                "agent": "Lead",
                "final_message": result[:8000],
            },
        )
    elif intent_route == "small_edit":
        await _verify_and_summarize_small_edit(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            result=result,
            tool_evidence=tool_evidence,
            sync_run_context=sync_run_context,
            turn_runner=turn_runner,
            evidence_collector=evidence_collector,
        )

    await turn_runner(
        thread_id,
        workspace_dir,
        action={
            "type": "finish",
            "goal": "Finish the lightweight Lead run.",
            "agent": "Lead",
            "final_message": result[:8000],
        },
    )
    return result


async def _verify_and_summarize_small_edit(
    *,
    thread_id: str,
    workspace_dir: str,
    result: str,
    tool_evidence: list[dict[str, Any]],
    sync_run_context: SyncRunContext,
    turn_runner: TurnRunner,
    evidence_collector: EvidenceCollector,
) -> None:
    evidence = evidence_collector(
        thread_id,
        workspace_dir,
        tool_calls=tool_evidence,
    )
    sync_run_context(thread_id, workspace_dir)
    if not evidence.ready:
        await turn_runner(
            thread_id,
            workspace_dir,
            action={
                "type": "fail",
                "goal": evidence.reason,
                "agent": "Lead",
                "final_message": evidence.reason,
                "context_requirements": {
                    "changed_files": evidence.changed_files,
                    "failed_calls": evidence.failed_calls,
                },
            },
        )
        raise RuntimeError(evidence.reason)

    await turn_runner(
        thread_id,
        workspace_dir,
        action={
            "type": "run_checks",
            "goal": "Verify the small edit from Diff and tool evidence.",
            "agent": "Lead",
            "context_requirements": {
                "changed_files": evidence.changed_files,
                "check_calls": evidence.check_calls,
                "diff_source": evidence.diff_source,
            },
        },
        executor=lambda _action, _context: {
            "executed": True,
            "result": "success",
            "evidence": evidence.model_dump(mode="json"),
        },
    )
    await turn_runner(
        thread_id,
        workspace_dir,
        action={
            "type": "summarize",
            "goal": "Summarize the completed small edit and its verification evidence.",
            "agent": "Lead",
            "final_message": result[:8000],
            "context_requirements": {
                "changed_files": evidence.changed_files,
                "write_call_count": len(evidence.write_calls),
                "check_call_count": len(evidence.check_calls),
            },
        },
    )
