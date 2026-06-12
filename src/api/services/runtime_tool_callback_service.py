"""Tool governance callbacks used by the streamed Agent runtime.

The callback object owns per-run policy decisions, approvals, evidence, ledger
records, and derived events. Decisions are queued per tool name so parallel
calls of different tools cannot consume each other's policy result.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.api.services.agent_loop_state_service import (
    append_loop_step,
    check_loop_tool_guard,
)
from src.api.services.run_ledger_service import (
    record_tool_call_finish,
    record_tool_call_start,
)
from src.api.services.runtime_approval_wait_service import (
    RuntimeApprovalWaitContext,
    resolve_runtime_tool_approval,
)
from src.api.services.tool_events import capability_trace_for_tool, derive_agenthub_events
from src.runtime.run_state import RunStatus
from src.tools.file_ops import pop_filetools_backend_event
from src.tools.filetools_evidence import build_file_tool_evidence
from src.tools.tool_result import is_tool_error_output

EmitEvent = Callable[..., Any]
EmitActivity = Callable[..., Any]
TransitionState = Callable[[str, str, RunStatus], None]
SyncRunContext = Callable[[str, str], Any]
EmitStageUpdates = Callable[[str, str, list[dict[str, Any]] | None], None]
ShouldCancel = Callable[[str], bool]
TokenMetrics = Callable[[], tuple[int, int]]


@dataclass
class RuntimeToolCallbacks:
    thread_id: str
    workspace_dir: str
    policy_runtime: Any
    change_tracker: Any
    active_runs: dict[str, Any]
    runs_lock: Any
    metrics_collector: Any
    emit_event: EmitEvent
    emit_activity: EmitActivity
    transition_state: TransitionState
    sync_run_context: SyncRunContext
    emit_stage_updates: EmitStageUpdates
    should_cancel: ShouldCancel
    token_metrics: TokenMetrics = lambda: (0, 0)
    uses_runtime_turn_loop: bool = False
    approval_timeout_seconds: float = 120.0
    approved_tools: set[str] = field(default_factory=set)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    _pending_decisions: dict[str, deque[Any]] = field(
        default_factory=lambda: defaultdict(deque)
    )

    def _remember_decision(self, tool_name: str, decision: Any) -> None:
        self._pending_decisions[tool_name].append(decision)

    def _take_decision(self, tool_name: str, tool_input: dict[str, Any]) -> Any:
        queue = self._pending_decisions.get(tool_name)
        if queue:
            decision = queue.popleft()
            if not queue:
                self._pending_decisions.pop(tool_name, None)
            return decision
        return self.policy_runtime.check(tool_name, tool_input)

    async def on_tool_check(self, tool_name: str, tool_input: dict[str, Any]) -> Any:
        """Check loop guard, policy, and optional user approval for one tool."""
        try:
            loop_decision = check_loop_tool_guard(
                self.thread_id,
                self.workspace_dir,
                tool_name,
                tool_input,
            )
        except Exception:
            loop_decision = None
        if loop_decision is not None:
            self._remember_decision(tool_name, loop_decision)
            self.emit_event(
                thread_id=self.thread_id,
                event_type="loop_guard_blocked",
                title=f"Loop Guard 拦截工具: {tool_name}",
                content=loop_decision.reason,
                agent="lead",
                payload={"tool": tool_name, "decision": loop_decision.to_dict()},
                workspace_dir=self.workspace_dir,
            )
            return loop_decision

        decision = self.policy_runtime.check(tool_name, tool_input)
        trace = capability_trace_for_tool(tool_name)
        target = self._tool_target(tool_input)
        action_text = f"准备调用 {tool_name}"
        if target:
            action_text = f"{action_text}: {str(target)[:120]}"
        self.emit_activity(
            thread_id=self.thread_id,
            agent=str(trace.get("agent") or "lead"),
            title=f"{trace.get('agent') or 'Agent'} 正在准备工具调用",
            content=action_text,
            workspace_dir=self.workspace_dir,
            payload={
                "phase": "tool_check",
                "tool": tool_name,
                "target": target,
                "decision": decision.to_dict(),
                "capability_trace": trace,
            },
        )
        if decision.allowed and decision.requires_approval and tool_name in self.approved_tools:
            decision.requires_approval = False
            decision.status = "auto_allowed"
            decision.reason = f"{tool_name} 已在本次运行中批准，后续同类调用自动放行。"
        self._remember_decision(tool_name, decision)
        if not decision.allowed:
            self.emit_event(
                thread_id=self.thread_id,
                event_type="tool_policy_blocked",
                title=f"工具被策略拦截: {tool_name}",
                content=decision.reason,
                agent="system",
                payload={"tool": tool_name, "decision": decision.to_dict()},
                workspace_dir=self.workspace_dir,
            )
            return decision

        if decision.requires_approval:
            await self._resolve_approval(tool_name, tool_input, decision)
        return decision

    async def _resolve_approval(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        decision: Any,
    ) -> None:
        resolved = await resolve_runtime_tool_approval(
            context=RuntimeApprovalWaitContext(
                thread_id=self.thread_id,
                workspace_dir=self.workspace_dir,
                emit_event=self.emit_event,
                emit_activity=self.emit_activity,
                transition_state=self.transition_state,
                should_cancel=self.should_cancel,
                timeout_seconds=self.approval_timeout_seconds,
            ),
            tool_name=tool_name,
            tool_input=tool_input,
            decision=decision,
        )
        if resolved.get("status") == "approved":
            self.approved_tools.add(tool_name)

    def on_tool_call(self, tool_name: str, tool_input: dict[str, Any], output: str) -> None:
        """Persist evidence and domain events for a completed allowed tool call."""
        decision = self._take_decision(tool_name, tool_input)
        if not decision.allowed:
            return

        ok_flag = not is_tool_error_output(output)
        filetool_evidence = build_file_tool_evidence(tool_name, tool_input, output)
        filetools_backend_event = pop_filetools_backend_event() if filetool_evidence else None
        if (
            filetool_evidence is not None
            and filetools_backend_event
            and filetool_evidence.get("backend") == "unknown"
            and filetools_backend_event.get("backend") in {"go", "python"}
        ):
            filetool_evidence["backend"] = filetools_backend_event["backend"]
        evidence_item = {
            "tool": tool_name,
            "input": dict(tool_input or {}),
            "output": str(output or "")[:5000],
            "ok": ok_flag,
        }
        if filetool_evidence is not None:
            evidence_item["filetool_evidence"] = filetool_evidence
        if filetools_backend_event is not None:
            evidence_item["filetools_backend_event"] = filetools_backend_event
        self.evidence.append(evidence_item)
        adaptation = self.policy_runtime.record(tool_name, ok=ok_flag)
        trace = capability_trace_for_tool(tool_name)
        if adaptation:
            self.emit_event(
                thread_id=self.thread_id,
                event_type="tool_policy_adapted",
                title=f"策略自适应: {adaptation.get('type', '')}",
                content=adaptation.get("reason", ""),
                agent="system",
                payload={**adaptation, "budget": self.policy_runtime.budget.to_dict()},
                workspace_dir=self.workspace_dir,
            )
        if tool_name in {"write_file", "edit_file"}:
            file_path = (tool_input or {}).get("file_path") or (tool_input or {}).get("path")
            if file_path:
                self.change_tracker.record_change(str(file_path), trace["agent"], "modify")

        self.emit_event(
            thread_id=self.thread_id,
            event_type="tool_policy_checked",
            title=f"策略检查: {tool_name}",
            content=decision.reason,
            agent="system",
            payload={
                "tool": tool_name,
                "decision": decision.to_dict(),
                "budget": self.policy_runtime.budget.to_dict(),
            },
            workspace_dir=self.workspace_dir,
        )
        stage_updates, current_stage_id = self._apply_tool_to_run(
            tool_name,
            trace,
            ok_flag,
            output,
        )
        input_tokens, output_tokens = self.token_metrics()
        self.emit_activity(
            thread_id=self.thread_id,
            agent=trace["agent"].lower(),
            title=f"{trace['agent']} 完成工具调用",
            content=(output or "")[:240],
            workspace_dir=self.workspace_dir,
            payload={
                "phase": "tool_finished",
                "tool": tool_name,
                "ok": ok_flag,
                "stage_id": current_stage_id,
                "capability_trace": trace,
            },
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        self.sync_run_context(self.thread_id, self.workspace_dir)
        self.emit_stage_updates(self.thread_id, self.workspace_dir, stage_updates)

        call_id = self._persist_ledger(tool_name, tool_input, output, ok_flag, current_stage_id)
        loop_record = self._persist_loop_action(tool_name, tool_input, output, ok_flag, trace, call_id)
        metrics = self.metrics_collector.dump_summary()
        self.emit_event(
            thread_id=self.thread_id,
            event_type="tool_call_finished",
            title=f"能力调用：{trace['capability_name']}",
            content=output[:1000] if output else "",
            agent=trace["agent"].lower(),
            payload={
                "tool": tool_name,
                "input": tool_input,
                "output": output[:5000] if output else "",
                "ok": ok_flag,
                "metrics": metrics,
                "capability_trace": trace,
                "stage_id": current_stage_id,
                "loop_step_id": loop_record.get("loop_step_id", ""),
                "loop_action_type": loop_record.get("action_type", ""),
                "loop_recorded": loop_record.get("recorded", False),
                "loop_step_error": loop_record.get("error", ""),
                "filetool_evidence": filetool_evidence,
                "filetools_backend_event": filetools_backend_event,
            },
            legacy_event={
                "type": "tool_call",
                "tool": tool_name,
                "input": tool_input,
                "output": output[:500] if output else "",
                "metrics": metrics,
            },
            workspace_dir=self.workspace_dir,
        )
        self._emit_filetools_backend_events(filetools_backend_event, trace, tool_name, tool_input)
        self._emit_filetool_evidence_events(filetool_evidence, trace)
        for derived_event in derive_agenthub_events(
            tool_name=tool_name,
            tool_input=tool_input,
            output=output,
            workspace_dir=self.workspace_dir,
            thread_id=self.thread_id,
        ):
            self.emit_event(
                thread_id=self.thread_id,
                workspace_dir=self.workspace_dir,
                **derived_event,
            )

    def _emit_filetools_backend_events(
        self,
        backend_event: dict[str, Any] | None,
        trace: dict[str, str],
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> None:
        if not backend_event or not backend_event.get("fallback"):
            return
        target = self._tool_target(tool_input)
        reason = str(backend_event.get("reason") or "")
        self.emit_event(
            thread_id=self.thread_id,
            event_type="filetools_backend_fallback",
            title=f"Go filetools fallback：{tool_name}",
            content=reason[:1000],
            agent=trace["agent"].lower(),
            payload={
                "tool": tool_name,
                "target": target,
                "capability_trace": trace,
                **backend_event,
            },
            workspace_dir=self.workspace_dir,
        )

    def _emit_filetool_evidence_events(
        self,
        filetool_evidence: dict[str, Any] | None,
        trace: dict[str, str],
    ) -> None:
        if not filetool_evidence or filetool_evidence.get("error"):
            return
        path = str(filetool_evidence.get("path") or "")
        backup_path = filetool_evidence.get("backup_path")
        if backup_path:
            self.emit_event(
                thread_id=self.thread_id,
                event_type="file_backup",
                title=f"文件备份：{path or backup_path}",
                content=str(backup_path),
                agent=trace["agent"].lower(),
                payload={
                    "path": path,
                    "backup_path": backup_path,
                    "backend": filetool_evidence.get("backend"),
                    "operation": filetool_evidence.get("operation"),
                },
                workspace_dir=self.workspace_dir,
            )
        if filetool_evidence.get("operation") == "rollback" and filetool_evidence.get("changed"):
            self.emit_event(
                thread_id=self.thread_id,
                event_type="file_rollback",
                title=f"文件回滚：{path}",
                content=path,
                agent=trace["agent"].lower(),
                payload={
                    "path": path,
                    "backend": filetool_evidence.get("backend"),
                    "operation": "rollback",
                },
                workspace_dir=self.workspace_dir,
            )

    def _apply_tool_to_run(
        self,
        tool_name: str,
        trace: dict[str, str],
        ok_flag: bool,
        output: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        with self.runs_lock:
            current_run = self.active_runs.get(self.thread_id)
            stage_updates = (
                current_run.apply_tool_event(
                    tool_name=tool_name,
                    capability_id=trace["capability_id"],
                    agent=trace["agent"],
                    ok=ok_flag,
                    output=output or "",
                )
                if current_run
                else []
            )
            current_stage_id = (
                current_run.metadata.get("lifecycle", {}).get("current_stage_id")
                if current_run
                else None
            )
        return stage_updates, current_stage_id

    def _persist_ledger(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        output: str,
        ok_flag: bool,
        current_stage_id: str | None,
    ) -> str:
        try:
            record = record_tool_call_start(
                thread_id=self.thread_id,
                tool_name=tool_name,
                tool_input=tool_input,
                step_id=current_stage_id or "",
                workspace_dir=self.workspace_dir,
            )
            record_tool_call_finish(
                call_id=record.call_id,
                thread_id=self.thread_id,
                output=output,
                ok=ok_flag,
                workspace_dir=self.workspace_dir,
            )
            return record.call_id
        except Exception:
            return ""

    def _persist_loop_action(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        output: str,
        ok_flag: bool,
        trace: dict[str, str],
        call_id: str,
    ) -> dict[str, Any]:
        action_type = _loop_action_type_for_tool(tool_name, tool_input)
        loop_action = {
            "type": action_type,
            "goal": f"Call {tool_name}.",
            "agent": trace["agent"],
            "tool_call": {"tool": tool_name, "input": tool_input} if action_type == "call_tool" else None,
            "context_requirements": {
                "tool_result": (output or "")[:1000],
                "tool_ok": ok_flag,
                "event_id": call_id or None,
            },
        }
        try:
            if self.uses_runtime_turn_loop:
                from src.api.services.agent_loop_controller_service import run_loop_controller_step

                result = run_loop_controller_step(
                    self.thread_id,
                    self.workspace_dir,
                    action=loop_action,
                    commit=True,
                    auto_repair=False,
                    status="completed" if ok_flag else "failed",
                    summary=(output or "")[:500],
                    event_id=call_id or None,
                )
                step = result.get("step") if isinstance(result, dict) else None
                return {
                    "recorded": bool(result.get("committed")) if isinstance(result, dict) else False,
                    "loop_step_id": _loop_step_id_from_step(step),
                    "action_type": action_type,
                    "error": "",
                }
            else:
                state = append_loop_step(
                    self.thread_id,
                    self.workspace_dir,
                    phase="act",
                    status="completed" if ok_flag else "failed",
                    action=loop_action,
                    summary=(output or "")[:500],
                    event_id=call_id or None,
                )
                return {
                    "recorded": True,
                    "loop_step_id": state.steps[-1].id if getattr(state, "steps", None) else "",
                    "action_type": action_type,
                    "error": "",
                }
        except Exception as exc:
            error = str(exc)
            try:
                self.emit_event(
                    thread_id=self.thread_id,
                    event_type="agent_loop_step_record_failed",
                    title=f"工具动作未能写入 Agent Loop: {tool_name}",
                    content=error[:1000],
                    agent="lead",
                    payload={
                        "tool": tool_name,
                        "action_type": action_type,
                        "agent": trace.get("agent") or "Lead",
                        "call_id": call_id,
                        "error": error,
                    },
                    workspace_dir=self.workspace_dir,
                )
            except Exception:
                pass
            return {
                "recorded": False,
                "loop_step_id": "",
                "action_type": action_type,
                "error": error,
            }

    @staticmethod
    def _tool_target(tool_input: dict[str, Any]) -> Any:
        if not isinstance(tool_input, dict):
            return ""
        return (
            tool_input.get("path")
            or tool_input.get("filename")
            or tool_input.get("command")
            or tool_input.get("query")
            or ""
        )


def _loop_action_type_for_tool(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "run_tests":
        return "run_checks"
    if tool_name in {"bash", "run_command"}:
        command = ""
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command") or "").lower()
        if any(marker in command for marker in ("pytest", "npm test", "pnpm test", "yarn test", "go test", "ruff", "mypy", "lint")):
            return "run_checks"
    return "call_tool"


def _loop_step_id_from_step(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("id") or "")
    return str(getattr(step, "id", "") or "")
