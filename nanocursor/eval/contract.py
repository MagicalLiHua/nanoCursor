from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "1"

TOOL_NAMES = (
    "repo_list",
    "repo_read",
    "repo_search",
    "repo_write",
    "repo_replace",
    "repo_delete",
    "repo_diff",
    "command_run",
)

ToolName = Literal[
    "repo_list",
    "repo_read",
    "repo_search",
    "repo_write",
    "repo_replace",
    "repo_delete",
    "repo_diff",
    "command_run",
]

ISSUE_AGENT_SYSTEM_PROMPT = (Path(__file__).with_name("issue_agent_system_prompt.txt")).read_text(
    encoding="utf-8"
).rstrip("\n")


class StrictParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepoListParams(StrictParams):
    path: str = Field(default=".", max_length=2_000)
    depth: int = Field(default=2, ge=1, le=4)


class RepoReadParams(StrictParams):
    path: str = Field(min_length=1, max_length=2_000)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=160, ge=1, le=240)


class RepoSearchParams(StrictParams):
    query: str = Field(min_length=1, max_length=200)
    path: str = Field(default=".", max_length=2_000)


class RepoWriteParams(StrictParams):
    path: str = Field(min_length=1, max_length=2_000)
    content: str = Field(min_length=1, max_length=120_000)


class RepoReplaceParams(StrictParams):
    path: str = Field(min_length=1, max_length=2_000)
    old_text: str = Field(min_length=1, max_length=60_000)
    new_text: str = Field(max_length=60_000)
    expected_occurrences: int = Field(default=1, ge=1, le=20)


class RepoDeleteParams(StrictParams):
    path: str = Field(min_length=1, max_length=2_000)


class RepoDiffParams(StrictParams):
    pass


class CommandRunParams(StrictParams):
    argv: list[Annotated[str, Field(min_length=1, max_length=2_000)]] = Field(
        min_length=1,
        max_length=80,
    )
    timeout_ms: int | None = Field(default=None, ge=10_000, le=180_000)
