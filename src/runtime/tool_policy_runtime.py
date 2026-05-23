"""ToolPolicyRuntime: enforce tool policy at call time."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from src.runtime.run_budget import RunBudget


@dataclass
class ToolPolicyDecision:
    tool: str = ""
    allowed: bool = True
    requires_approval: bool = False
    reason: str = ""
    budget_exceeded: list[str] = field(default_factory=list)
    decision_id: str = ""
    risk_level: str = "medium"
    status: Literal["pending", "approved", "rejected", "auto_allowed", "blocked"] = "auto_allowed"
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.decision_id:
            self.decision_id = f"approval_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = time.time()
        # Derive status from allowed / requires_approval flags
        if not self.allowed:
            self.status = "blocked"
        elif self.requires_approval:
            self.status = "pending"
        # else: keep "auto_allowed"

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "tool": self.tool,
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "status": self.status,
            "budget_exceeded": self.budget_exceeded,
            "created_at": self.created_at,
        }


class ToolPolicyRuntime:
    """Enforce tool policy and budget at runtime."""

    def __init__(self, policy: dict[str, Any] | None = None, budget: RunBudget | None = None):
        p = policy or {}
        self.allowed_tools: list[str] = list(p.get("allowed_tools", []))
        self.denied_tools: list[str] = list(p.get("denied_tools", []))
        self.approval_required: list[str] = list(p.get("approval_required", []))
        self.risk_level: str = p.get("risk_level", "medium")
        self.budget = budget or RunBudget()
        self.decisions: list[dict] = []

    def check(self, tool_name: str) -> ToolPolicyDecision:
        # 1. Denied
        if tool_name in self.denied_tools:
            d = ToolPolicyDecision(
                tool=tool_name, allowed=False,
                reason=f"{tool_name} 在当前策略中被禁止。",
                risk_level=self.risk_level,
            )
            self.decisions.append(d.to_dict())
            return d

        # 2. Not in allowed (if allowed list is non-empty)
        if self.allowed_tools and tool_name not in self.allowed_tools:
            d = ToolPolicyDecision(
                tool=tool_name, allowed=False,
                reason=f"{tool_name} 不在允许列表中。",
                risk_level=self.risk_level,
            )
            self.decisions.append(d.to_dict())
            return d

        # 3. Budget exceeded
        exceeded = self.budget.exceeded_for(tool_name)
        if exceeded:
            d = ToolPolicyDecision(
                tool=tool_name, allowed=False,
                reason=f"预算超限: {exceeded}", budget_exceeded=exceeded,
                risk_level=self.risk_level,
            )
            self.decisions.append(d.to_dict())
            return d

        # 4. Requires approval
        if tool_name in self.approval_required:
            d = ToolPolicyDecision(
                tool=tool_name,
                allowed=True,
                requires_approval=True,
                status="pending",
                reason=f"{tool_name} 在当前策略 ({self.risk_level}) 下需要审批。",
                risk_level=self.risk_level,
            )
            self.decisions.append(d.to_dict())
            return d

        # 5. Allowed
        d = ToolPolicyDecision(
            tool=tool_name, allowed=True, status="auto_allowed",
            reason="ok", risk_level=self.risk_level,
        )
        self.decisions.append(d.to_dict())
        return d

    def record(self, tool_name: str, ok: bool) -> None:
        self.budget.record_tool(tool_name)

    def to_dict(self) -> dict:
        return {
            "policy": {
                "allowed_tools": self.allowed_tools,
                "denied_tools": self.denied_tools,
                "approval_required": self.approval_required,
                "risk_level": self.risk_level,
            },
            "budget": self.budget.to_dict(),
            "decisions": self.decisions[-20:],
        }

    def violations(self) -> list[dict]:
        return [d for d in self.decisions if not d.get("allowed", True)]
