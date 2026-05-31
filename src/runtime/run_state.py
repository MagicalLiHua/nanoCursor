"""Formal run state machine with validated transitions."""

from __future__ import annotations

from enum import Enum
from typing import ClassVar


class RunStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    VALIDATING = "validating"
    CANCELLING = "cancelling"       # user requested cancel, waiting for halt
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"     # server shutdown mid-run
    CANCELLED = "cancelled"         # confirm cancelled
    RECOVERING = "recovering"       # re-entering after interrupt

    # Backward-compat alias
    CANCEL_REQUESTED = "cancelling"


class RunMode(str, Enum):
    NORMAL = "normal"
    CONVERSATION = "conversation"
    DEMO = "demo"
    BENCHMARK = "benchmark"
    EVAL = "eval"
    REMEDIATION = "remediation"


ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.CREATED:          {RunStatus.PLANNING, RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.PLANNING:         {RunStatus.WAITING_APPROVAL, RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLING, RunStatus.CANCELLED},
    RunStatus.WAITING_APPROVAL: {RunStatus.RUNNING, RunStatus.PLANNING, RunStatus.CANCELLING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.RUNNING:          {RunStatus.VALIDATING, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLING, RunStatus.INTERRUPTED},
    RunStatus.VALIDATING:       {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLING, RunStatus.INTERRUPTED},
    RunStatus.CANCELLING:       {RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.FAILED:           {RunStatus.RECOVERING, RunStatus.CANCELLED},
    RunStatus.RECOVERING:       {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.INTERRUPTED:      {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.RECOVERING},
}

TERMINAL_STATUSES: frozenset[RunStatus] = frozenset({
    RunStatus.COMPLETED,
    RunStatus.CANCELLED,
    RunStatus.FAILED,
    RunStatus.INTERRUPTED,
})


class RunStateMachine:
    """Validates and records run state transitions."""

    def __init__(self, initial_status: RunStatus = RunStatus.CREATED) -> None:
        self.status: RunStatus = initial_status
        self._history: list[RunStatus] = [initial_status]

    def transition(self, new_status: RunStatus) -> RunStatus:
        self.validate_transition(self.status, new_status)
        self.status = new_status
        self._history.append(new_status)
        return self.status

    def can_transition(self, new_status: RunStatus) -> bool:
        try:
            self.validate_transition(self.status, new_status)
            return True
        except ValueError:
            return False

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def current_status(self) -> str:
        return self.status.value

    def history(self) -> list[str]:
        return [s.value for s in self._history]

    @classmethod
    def validate_transition(cls, from_status: RunStatus, to_status: RunStatus) -> None:
        valid = ALLOWED_TRANSITIONS.get(from_status)
        if valid is None:
            raise ValueError(f"未知状态: {from_status!r}")
        if to_status not in valid:
            raise ValueError(
                f"不允许的状态转移: {from_status.value!r} -> {to_status.value!r}"
            )

    def to_dict(self) -> dict:
        return {"status": self.status.value, "history": self.history()}
