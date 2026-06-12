"""Structured Lead decision protocol.

This module defines the small action vocabulary used by the Lead loop.  The
runtime can still be implemented by the existing agent engine, but every major
decision should be recorded through these models so runs are inspectable and
recoverable.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

LeadActionType = Literal[
    "answer",
    "ask_clarification",
    "inspect_project",
    "create_tasks",
    "spawn_agent",
    "merge_agent_result",
    "call_tool",
    "request_approval",
    "run_checks",
    "summarize",
    "finish",
    "fail",
]


class LeadAction(BaseModel):
    """One validated Lead-loop action."""

    type: LeadActionType
    goal: str = Field(default="", max_length=1000)
    agent: str = Field(default="Lead", max_length=120)
    task_id: str | None = Field(default=None, max_length=160)
    tool_call: dict[str, Any] | None = None
    context_requirements: dict[str, Any] = Field(default_factory=dict)
    approval: dict[str, Any] | None = None
    final_message: str = Field(default="", max_length=8000)
