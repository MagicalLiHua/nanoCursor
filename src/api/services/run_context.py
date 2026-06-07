"""Runtime context objects for active nanoCursor runs."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunContext:
    """In-memory state for one active run.

    The class keeps the old dict-style accessors used by legacy runtime while
    giving the runtime a named boundary for workspace, conversation, team, and
    approval state.
    """

    thread_id: str
    workspace_dir: str
    queue: queue.Queue
    status: str = "running"
    thread: threading.Thread | None = None
    mode: str = "agenthub_delivery"
    conversation_id: str | None = None
    team: list[dict[str, Any]] = field(default_factory=list)
    execution_plan: dict[str, Any] = field(default_factory=dict)
    approval_event: threading.Event | None = None
    approval_decision: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    change_tracker: Any = None

    def __post_init__(self) -> None:
        self._ensure_lifecycle()

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, self.metadata.get(key, default))

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return self.metadata[key]

    def __setitem__(self, key: str, value: Any) -> None:
        if hasattr(self, key):
            setattr(self, key, value)
            return
        self.metadata[key] = value

    def bind_conversation(self, conversation_id: str, team: list[dict[str, Any]] | None = None) -> None:
        self.conversation_id = conversation_id
        self.team = list(team or [])

    def set_execution_plan(self, execution_plan: dict[str, Any]) -> None:
        self.execution_plan = dict(execution_plan or {})
        self._ensure_lifecycle()

    def set_status(self, status: str) -> None:
        self.status = status

    def resolve_approval(self, decision: str) -> None:
        self.approval_decision = decision
        if self.approval_event:
            self.approval_event.set()

    def session_metadata(self) -> dict[str, Any]:
        """Return durable metadata that belongs in EventStore session files."""
        self._ensure_lifecycle()
        data: dict[str, Any] = {
            "mode": self.mode,
            "conversation_id": self.conversation_id,
            "team": self.team,
            "execution_plan": self.execution_plan,
            "lifecycle": self.metadata.get("lifecycle"),
            **self.metadata,
        }
        return {key: value for key, value in data.items() if value not in (None, [], {})}

    def _ensure_lifecycle(self) -> None:
        """Ensure execution_plan stages/tasks have durable lifecycle fields."""
        if not isinstance(self.execution_plan, dict):
            self.execution_plan = {}
        stages = self.execution_plan.get("stages")
        tasks = self.execution_plan.get("tasks")
        if not isinstance(stages, list):
            stages = []
            if "stages" in self.execution_plan:
                self.execution_plan["stages"] = stages
        if not isinstance(tasks, list):
            tasks = []
            if "tasks" in self.execution_plan:
                self.execution_plan["tasks"] = tasks

        now = time.time()
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            stage.setdefault("status", "pending")
            stage.setdefault("started_at", None)
            stage.setdefault("completed_at", None)
            stage.setdefault("failed_at", None)
            stage.setdefault("tool_evidence", [])
            stage.setdefault("failure", None)

        for task in tasks:
            if not isinstance(task, dict):
                continue
            task.setdefault("status", "pending")
            task.setdefault("started_at", None)
            task.setdefault("completed_at", None)
            task.setdefault("failed_at", None)
            task.setdefault("tool_evidence", [])
            task.setdefault("failure", None)

        self.metadata.setdefault(
            "lifecycle",
            {
                "status": self.status,
                "current_stage_id": None,
                "started_at": now,
                "completed_at": None,
                "failed_stage_id": None,
                "failure": None,
            },
        )

    def _task_for_stage(self, stage_id: str) -> dict[str, Any] | None:
        for task in self.execution_plan.get("tasks", []):
            if isinstance(task, dict) and task.get("id", "").endswith(f"-{stage_id}"):
                return task
        return None

    def _set_stage_status(
        self,
        stage: dict[str, Any],
        status: str,
        reason: str = "",
        failure: str | None = None,
    ) -> dict[str, Any] | None:
        previous = stage.get("status", "pending")
        if previous == status and not failure:
            return None

        now = time.time()
        stage["status"] = status
        if status == "running" and not stage.get("started_at"):
            stage["started_at"] = now
        if status == "completed":
            stage["completed_at"] = now
            stage["failure"] = None
        if status == "failed":
            stage["failed_at"] = now
            stage["failure"] = failure or reason or "阶段执行失败"
        if status == "skipped":
            stage["completed_at"] = now

        task = self._task_for_stage(str(stage.get("id", "")))
        if task is not None:
            task["status"] = status
            if status == "running" and not task.get("started_at"):
                task["started_at"] = stage.get("started_at") or now
            if status in {"completed", "skipped"}:
                task["completed_at"] = stage.get("completed_at") or now
                task["failure"] = None
            if status == "failed":
                task["failed_at"] = stage.get("failed_at") or now
                task["failure"] = stage.get("failure")

        self.metadata["lifecycle"]["current_stage_id"] = stage.get("id") if status == "running" else self.metadata["lifecycle"].get("current_stage_id")
        return {
            "stage_id": stage.get("id"),
            "title": stage.get("title"),
            "owner": stage.get("owner"),
            "status": status,
            "previous_status": previous,
            "reason": reason,
            "failure": stage.get("failure"),
        }

    @staticmethod
    def _stage_has_recovery_evidence(stage: dict[str, Any]) -> bool:
        failed_at = stage.get("failed_at")
        try:
            failed_ts = float(failed_at)
        except (TypeError, ValueError):
            return False
        for evidence in stage.get("tool_evidence", []):
            if not isinstance(evidence, dict) or not bool(evidence.get("ok")):
                continue
            try:
                evidence_ts = float(evidence.get("timestamp"))
            except (TypeError, ValueError):
                continue
            if evidence_ts >= failed_ts:
                return True
        return False

    def start_first_stage(self) -> list[dict[str, Any]]:
        """Mark the first planned stage as running."""
        self._ensure_lifecycle()
        stages = [stage for stage in self.execution_plan.get("stages", []) if isinstance(stage, dict)]
        if not stages:
            return []
        update = self._set_stage_status(stages[0], "running", "run_started")
        return [update] if update else []

    def apply_tool_event(
        self,
        tool_name: str,
        capability_id: str,
        agent: str = "Lead",
        ok: bool = True,
        output: str = "",
    ) -> list[dict[str, Any]]:
        """Attach tool evidence to the most relevant stage and advance lifecycle."""
        self._ensure_lifecycle()
        stages = [stage for stage in self.execution_plan.get("stages", []) if isinstance(stage, dict)]
        if not stages:
            return []

        target_index = self._stage_index_for_capability(capability_id, agent)
        target = stages[target_index]
        updates: list[dict[str, Any]] = []

        for stage in stages[:target_index]:
            if stage.get("status") in {"pending", "running"}:
                update = self._set_stage_status(stage, "completed", f"advanced_before_{tool_name}")
                if update:
                    updates.append(update)

        if target.get("status") == "pending":
            update = self._set_stage_status(target, "running", f"tool:{tool_name}")
            if update:
                updates.append(update)

        evidence = {
            "tool": tool_name,
            "capability_id": capability_id,
            "agent": agent,
            "ok": ok,
            "timestamp": time.time(),
            "output_preview": (output or "")[:300],
        }
        target.setdefault("tool_evidence", []).append(evidence)
        target["tool_evidence"] = target["tool_evidence"][-12:]
        task = self._task_for_stage(str(target.get("id", "")))
        if task is not None:
            task.setdefault("tool_evidence", []).append(evidence)
            task["tool_evidence"] = task["tool_evidence"][-12:]

        if not ok:
            update = self._set_stage_status(target, "failed", f"tool_failed:{tool_name}", (output or "工具调用失败")[:500])
            self.metadata["lifecycle"]["failed_stage_id"] = target.get("id")
            self.metadata["lifecycle"]["failure"] = target.get("failure")
            if update:
                updates.append(update)

        return updates

    def finalize_lifecycle(self, final_status: str, failure: str = "") -> list[dict[str, Any]]:
        """Finalize all planned stages when the run reaches a terminal status."""
        self._ensure_lifecycle()
        updates: list[dict[str, Any]] = []
        stages = [stage for stage in self.execution_plan.get("stages", []) if isinstance(stage, dict)]

        if final_status == "completed":
            for stage in stages:
                if stage.get("status") == "failed" and self._stage_has_recovery_evidence(stage):
                    update = self._set_stage_status(stage, "completed", "recovered_after_tool_failure")
                    if update:
                        updates.append(update)
                if stage.get("status") in {"pending", "running"}:
                    target_status = "completed" if stage.get("required", True) else "skipped"
                    update = self._set_stage_status(stage, target_status, "run_completed")
                    if update:
                        updates.append(update)
            if not any(stage.get("status") == "failed" for stage in stages):
                self.metadata["lifecycle"]["failed_stage_id"] = None
                self.metadata["lifecycle"]["failure"] = None
            self.metadata["lifecycle"]["completed_at"] = time.time()
            self.metadata["lifecycle"]["status"] = "completed"
        elif final_status in {"failed", "cancelled"}:
            failing_stage = next((stage for stage in stages if stage.get("status") == "failed"), None)
            if failing_stage is None:
                failing_stage = next((stage for stage in stages if stage.get("status") == "running"), None)
            if failing_stage is None and final_status == "cancelled":
                failing_stage = next((stage for stage in stages if stage.get("status") == "pending"), None)
            if failing_stage is not None and failing_stage.get("status") != "failed":
                update = self._set_stage_status(
                    failing_stage,
                    "failed",
                    f"run_{final_status}",
                    failure or f"运行状态: {final_status}",
                )
                if update:
                    updates.append(update)
                self.metadata["lifecycle"]["failed_stage_id"] = failing_stage.get("id")
                self.metadata["lifecycle"]["failure"] = failing_stage.get("failure")
            elif failing_stage is not None:
                self.metadata["lifecycle"]["failed_stage_id"] = failing_stage.get("id")
                self.metadata["lifecycle"]["failure"] = failing_stage.get("failure")
            for stage in stages:
                if stage.get("status") == "pending":
                    update = self._set_stage_status(stage, "skipped", f"run_{final_status}")
                    if update:
                        updates.append(update)
            self.metadata["lifecycle"]["completed_at"] = time.time()
            self.metadata["lifecycle"]["status"] = final_status

        self.status = final_status
        return updates

    def _stage_index_for_capability(self, capability_id: str, agent: str = "") -> int:
        stages = [stage for stage in self.execution_plan.get("stages", []) if isinstance(stage, dict)]
        if not stages:
            return 0

        for index, stage in enumerate(stages):
            capabilities = [str(item) for item in stage.get("capabilities", [])]
            if capability_id in capabilities:
                return index

        agent_text = str(agent).lower()
        role_hints = {
            "planner": "plan",
            "coder": "implement",
            "tester": "verify",
            "reviewer": "diff_review",
            "designer": "design_review",
            "devops": "environment_check",
            "lead": "intake",
        }
        hinted_stage = next((stage_id for role, stage_id in role_hints.items() if role in agent_text), None)
        if hinted_stage:
            for index, stage in enumerate(stages):
                if stage.get("id") == hinted_stage:
                    return index

        current_stage_id = self.metadata.get("lifecycle", {}).get("current_stage_id")
        if current_stage_id:
            for index, stage in enumerate(stages):
                if stage.get("id") == current_stage_id:
                    return index
        return 0
