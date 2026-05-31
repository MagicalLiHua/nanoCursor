"""RunManager: lifecycle, workspace locking, interrupted detection."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from src.runtime.run_state import RunStateMachine, RunStatus, RunMode
from src.runtime.run_events import enrich_event
from src.infra import config as config_module


WRITE_MODES = frozenset({"agenthub_delivery", "agenthub_remediation", "default", ""})
READ_MODES = frozenset({"analysis_only", "read_only"})


def _to_run_status(value: str | RunStatus) -> RunStatus:
    """Coerce a string or RunStatus into a RunStatus enum member."""
    if isinstance(value, RunStatus):
        return value
    try:
        return RunStatus(value)
    except ValueError:
        # Try common aliases
        aliases: dict[str, str] = {
            "cancelled": "cancelled",
            "cancel_requested": "cancelling",
            "canceled": "cancelled",
        }
        mapped = aliases.get(value.lower(), value)
        return RunStatus(mapped)


def _is_write_mode(run_ctx: Any) -> bool:
    session = getattr(run_ctx, "metadata", {}) or {}
    mode = session.get("mode") or getattr(run_ctx, "mode", "")
    return mode not in READ_MODES


class RunManager:
    """Central registry of active runs with workspace locking."""

    def __init__(self) -> None:
        self._active: dict[str, Any] = {}
        self._state_machines: dict[str, RunStateMachine] = {}
        self._workspace_locks: dict[str, str] = {}
        self._lock = threading.RLock()

    # ---- registration ----

    def register(self, run_ctx: Any) -> bool:
        thread_id = run_ctx.thread_id
        workspace = str(Path(run_ctx.workspace_dir).resolve())

        with self._lock:
            if thread_id in self._active:
                raise ValueError(f"Run 已在活跃列表中: {thread_id}")
            if _is_write_mode(run_ctx):
                existing = self._workspace_locks.get(workspace)
                if existing and existing != thread_id:
                    if existing not in self._active:
                        del self._workspace_locks[workspace]
                    else:
                        raise ValueError(
                            f"工作区已被写入型 run 占用 (thread_id={existing})。"
                            f"同一工作区同时只允许一个写入型 run。"
                        )
                self._workspace_locks[workspace] = thread_id

            self._active[thread_id] = run_ctx
            sm = RunStateMachine(RunStatus.CREATED)
            sm.transition(RunStatus.RUNNING)
            self._state_machines[thread_id] = sm
        return True

    def unregister(self, thread_id: str) -> None:
        with self._lock:
            run_ctx = self._active.pop(thread_id, None)
            if run_ctx:
                workspace = str(Path(run_ctx.workspace_dir).resolve())
                if self._workspace_locks.get(workspace) == thread_id:
                    del self._workspace_locks[workspace]
            self._state_machines.pop(thread_id, None)

    # ---- queries ----

    def get(self, thread_id: str) -> Any | None:
        with self._lock:
            return self._active.get(thread_id)

    def get_state_machine(self, thread_id: str) -> RunStateMachine | None:
        with self._lock:
            return self._state_machines.get(thread_id)

    def get_workspace_for(self, thread_id: str) -> str:
        ctx = self.get(thread_id)
        if ctx:
            return ctx.workspace_dir
        return config_module.WORKSPACE_DIR

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for tid, ctx in self._active.items():
                sm = self._state_machines.get(tid)
                result.append({
                    "thread_id": tid,
                    "workspace_dir": ctx.workspace_dir,
                    "status": sm.status.value if sm else "unknown",
                    "is_write_mode": _is_write_mode(ctx),
                })
            return result

    # ---- state transitions ----

    def transition(self, thread_id: str, new_status: str | RunStatus) -> RunStateMachine:
        sm = self.get_state_machine(thread_id)
        if not sm:
            raise ValueError(f"Run 不在活跃列表中: {thread_id}")
        sm.transition(_to_run_status(new_status))
        return sm

    def request_cancel(self, thread_id: str) -> None:
        sm = self.get_state_machine(thread_id)
        if not sm:
            raise ValueError(f"Run 不在活跃列表中: {thread_id}")
        if sm.can_transition(RunStatus.CANCELLING):
            sm.transition(RunStatus.CANCELLING)
        ctx = self.get(thread_id)
        if ctx and hasattr(ctx, "set_status"):
            ctx.set_status("cancelling")

    def finalize(self, thread_id: str, final_status: str | RunStatus) -> None:
        sm = self.get_state_machine(thread_id)
        if sm:
            try:
                sm.transition(_to_run_status(final_status))
            except ValueError:
                pass

    # ---- interrupted detection ----

    def detect_interrupted(self, workspace_dir: str | None = None) -> list[str]:
        workspace = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
        runs_dir = workspace / ".nanocursor" / "runs"
        if not runs_dir.exists():
            return []

        interrupted: list[str] = []
        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            session_file = run_dir / "session.json"
            if not session_file.exists():
                continue
            try:
                session = json.loads(session_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            thread_id = session.get("thread_id", run_dir.name)
            status = session.get("status", "")

            if status != "running":
                continue

            with self._lock:
                if thread_id in self._active:
                    continue

            session["status"] = "interrupted"
            session["interrupted_at"] = time.time()
            try:
                session_file.write_text(
                    json.dumps(session, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                continue
            interrupted.append(thread_id)

        return interrupted
