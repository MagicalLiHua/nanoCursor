"""Unified action policy — all high-risk actions go through one pipeline.

R5: Every action that touches files, runs commands, or accesses external services
must pass through: path guard -> policy check -> approval if needed -> execute.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from src.runtime.tool_policy_runtime import classify_shell_command


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
    permission_level: str = "external_risky"


# ---- risk classification ----


def _classify_action_risk(kind: ActionKind, target: str = "", payload: dict[str, Any] | None = None) -> str:
    """Rule-based risk classification for any action kind + target."""
    permission_level = classify_action_permission(kind, target, payload=payload)
    if permission_level in {"read_only", "mcp_read"}:
        return "low"
    if permission_level in {"safe_write", "shell_safe", "git_safe"}:
        return "medium"
    return "high"


def classify_action_permission(kind: ActionKind, target: str = "", payload: dict[str, Any] | None = None) -> str:
    """Return the coarse permission level used by the action pipeline."""
    target_lower = (target or "").lower()
    payload = payload if isinstance(payload, dict) else {}

    if kind == ActionKind.READ_FILE:
        return "read_only"

    if kind == ActionKind.DELETE_FILE:
        return "risky_write"
    if kind == ActionKind.RUN_COMMAND:
        return classify_shell_command(target or "")
    if kind == ActionKind.RECOVERY_ACTION:
        return "risky_write"
    if kind == ActionKind.MCP_CALL:
        return classify_mcp_permission(target, payload)

    if kind == ActionKind.GIT_OPERATION:
        if any(kw in target_lower for kw in ("status", "diff", "log", "show", "branch")):
            return "git_safe"
        return "git_risky"

    if kind == ActionKind.WRITE_FILE:
        if any(seg in target_lower for seg in (
            ".env", "secret", "credential", "key", "token",
            "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        )):
            return "risky_write"
        return "safe_write"

    return "external_risky"


def classify_mcp_permission(target: str = "", payload: dict[str, Any] | None = None) -> str:
    """Classify an MCP tool call as read, write, or external risky.

    Unknown MCP tools stay high-risk. Only clearly read-only tool names or an
    explicit read-only payload hint are downgraded to ``mcp_read``.
    """
    payload = payload if isinstance(payload, dict) else {}
    explicit = str(
        payload.get("permission_level")
        or payload.get("permission")
        or payload.get("access")
        or payload.get("mode")
        or ""
    ).strip().lower()
    if explicit in {"mcp_read", "read", "readonly", "read_only"}:
        return "mcp_read"
    if explicit in {"mcp_write", "write", "mutation", "mutate"}:
        return "mcp_write"

    tool_name = _mcp_tool_name(target, payload)
    if not tool_name:
        return "external_risky"
    lowered = tool_name.lower().replace("-", "_")
    if any(token in lowered for token in _MCP_WRITE_TOKENS):
        return "mcp_write"
    if any(lowered.startswith(prefix) or f"_{prefix}" in lowered for prefix in _MCP_READ_PREFIXES):
        return "mcp_read"
    return "external_risky"


_MCP_READ_PREFIXES = (
    "list",
    "get",
    "read",
    "search",
    "find",
    "query",
    "fetch",
    "inspect",
    "describe",
    "resolve",
    "lookup",
)

_MCP_WRITE_TOKENS = (
    "create",
    "update",
    "delete",
    "remove",
    "write",
    "edit",
    "mutate",
    "submit",
    "approve",
    "merge",
    "commit",
    "push",
    "post",
    "upload",
    "install",
)


def _mcp_tool_name(target: str, payload: dict[str, Any]) -> str:
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "").strip()
    if tool_name:
        return tool_name
    raw = str(target or "").strip()
    for separator in ("::", "/", ":"):
        if separator in raw:
            return raw.split(separator, 1)[1].strip()
    return raw


def check_action(
    kind: ActionKind,
    target: str = "",
    thread_id: str = "",
    workspace_dir: str = "",
    payload: dict[str, Any] | None = None,
) -> ActionDecision:
    """Determine whether an action is allowed and whether it needs approval."""
    permission_level = classify_action_permission(kind, target, payload=payload)
    risk = _classify_action_risk(kind, target, payload=payload)

    if risk == "high":
        return ActionDecision(
            allowed=True,
            requires_approval=True,
            reason=f"{kind.value} 操作权限为 {permission_level}，风险等级为 {risk}，需要用户审批。",
            risk=risk,
            permission_level=permission_level,
        )

    # Medium risk: allowed but may need confirmation
    if risk == "medium":
        return ActionDecision(
            allowed=True,
            requires_approval=False,
            reason=f"{kind.value} 操作权限为 {permission_level}，风险等级为 {risk}，允许执行。",
            risk=risk,
            permission_level=permission_level,
        )

    return ActionDecision(
        allowed=True,
        requires_approval=False,
        reason=f"{kind.value} 操作权限为 {permission_level}，风险等级为 {risk}，直接执行。",
        risk=risk,
        permission_level=permission_level,
    )
