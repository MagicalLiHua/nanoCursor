"""Change set model — diff as a first-class object.

Every run collects file changes into a ChangeSet persisted as
changes.json under <workspace>/.nanocursor/runs/<thread_id>/.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ChangeSetStatus(str, Enum):
    COLLECTED = "collected"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class FilePatchSummary(BaseModel):
    path: str
    change_type: str = "modified"  # added | modified | deleted | renamed
    additions: int = 0
    deletions: int = 0
    hunks: int = 0
    summary: str = ""
    risk: str = "medium"  # low | medium | high
    related_requirement_ids: list[str] = Field(default_factory=list)


class ChangeSet(BaseModel):
    thread_id: str
    workspace_dir: str
    base_ref: str = ""
    status: ChangeSetStatus = ChangeSetStatus.COLLECTED
    files: list[FilePatchSummary] = Field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0
    generated_at: str = ""
