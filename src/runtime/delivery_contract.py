"""Run delivery contract — the unified, machine-readable result of every run.

Each completed/failed/cancelled run produces a DeliveryContract persisted as
delivery.json + delivery.md under <workspace>/.nanocursor/runs/<thread_id>/.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DeliveryStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"


class DeliveryFileChange(BaseModel):
    path: str
    change_type: str = "modified"  # added | modified | deleted | renamed
    additions: int = 0
    deletions: int = 0
    summary: str = ""
    risk: str = "medium"  # low | medium | high


class DeliveryVerification(BaseModel):
    command: str
    exit_code: int | None = None
    status: str = "not_run"  # passed | failed | skipped | not_run
    stdout_tail: str = ""
    stderr_tail: str = ""
    duration_ms: int = 0


class DeliveryAgentContribution(BaseModel):
    agent_id: str
    name: str = ""
    role: str = ""
    status: str = ""
    terminal_status: str = ""
    summary: str = ""
    evidence_count: int = 0
    risk_count: int = 0
    artifact_count: int = 0
    recommended_next_actions: list[str] = Field(default_factory=list)


class DeliveryContract(BaseModel):
    thread_id: str
    workspace_dir: str
    status: DeliveryStatus
    objective: str = ""
    summary: str = ""
    plan: list[dict[str, Any]] = Field(default_factory=list)
    agent_contributions: list[DeliveryAgentContribution] = Field(default_factory=list)
    changed_files: list[DeliveryFileChange] = Field(default_factory=list)
    verifications: list[DeliveryVerification] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    generated_at: str = ""
