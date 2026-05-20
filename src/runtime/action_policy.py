"""Unified action policy — all high-risk actions go through one pipeline.

R5: Every action that touches files, runs commands, or accesses external services
must pass through: path guard -> policy check -> approval if needed -> execute.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ActionKind(str, Enum):
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    DELETE_FILE = "delete_file"
    RUN_COMMAND = "run_command"
    GIT_OPERATION = "git_operation"
    MCP_CALL = "mcp_call"
    RECOVERY_ACTION = "recovery_action"


class ActionRequest(BaseModel):
    action_id: str = ""
    thread_id: str
    kind: ActionKind
    target: str = ""         # file path, command, or tool name
    payload: dict[str, Any] = Field(default_factory=dict)
    risk: str = "medium"     # low | medium | high

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if not self.action_id:
            object.__setattr__(self, "action_id", f"act_{uuid.uuid4().hex[:12]}")
        if not self.risk or self.risk == "medium":
            object.__setattr__(self, "risk", _classify_action_risk(self.kind, self.target))


class ActionDecision(BaseModel):
    allowed: bool
    requires_approval: bool
    reason: str
    approval_id: str | None = None
    risk: str = "medium"


# ---- risk classification ----


def _classify_action_risk(kind: ActionKind, target: str = "") -> str:
    """Rule-based risk classification for any action kind + target."""
    target_lower = (target or "").lower()

    # Always high risk
    if kind == ActionKind.DELETE_FILE:
        return "high"
    if kind == ActionKind.RUN_COMMAND:
        return "high"
    if kind == ActionKind.RECOVERY_ACTION:
        return "high"
    if kind == ActionKind.MCP_CALL:
        return "high"

    # Git operations: destructive ones are high
    if kind == ActionKind.GIT_OPERATION:
        if any(kw in target_lower for kw in ("discard", "reset", "hard", "force", "clean")):
            return "high"
        return "medium"

    # File writes: check target sensitivity
    if kind == ActionKind.WRITE_FILE:
        if any(seg in target_lower for seg in (
            ".env", "secret", "credential", "key", "token",
            "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        )):
            return "high"
        if any(target_lower.startswith(p) for p in (".github/workflows", "jenkinsfile", "dockerfile")):
            return "medium"
        return "medium"

    if kind == ActionKind.READ_FILE:
        return "low"

    return "medium"


def check_action(
    kind: ActionKind,
    target: str = "",
    thread_id: str = "",
    workspace_dir: str = "",
) -> ActionDecision:
    """Determine whether an action is allowed and whether it needs approval."""
    risk = _classify_action_risk(kind, target)

    if risk == "high":
        return ActionDecision(
            allowed=True,
            requires_approval=True,
            reason=f"{kind.value} 操作风险等级为 {risk}，需要用户审批。",
            risk=risk,
        )

    # Medium risk: allowed but may need confirmation
    if risk == "medium":
        return ActionDecision(
            allowed=True,
            requires_approval=False,
            reason=f"{kind.value} 操作风险等级为 {risk}，允许执行。",
            risk=risk,
        )

    return ActionDecision(
        allowed=True,
        requires_approval=False,
        reason=f"{kind.value} 操作风险等级为 {risk}，直接执行。",
        risk=risk,
    )
