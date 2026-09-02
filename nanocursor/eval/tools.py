from __future__ import annotations

import json

from pydantic import BaseModel

from nanocursor.eval.bridge_client import BridgeClient, BridgeError
from nanocursor.eval.contract import (
    CommandRunParams,
    RepoDeleteParams,
    RepoDiffParams,
    RepoListParams,
    RepoReadParams,
    RepoReplaceParams,
    RepoSearchParams,
    RepoWriteParams,
    ToolName,
)
from nanocursor.tools import ToolRegistry
from nanocursor.tools.base import Tool, ToolResult


class BridgeTool(Tool):
    name: ToolName
    is_concurrency_safe = False

    def __init__(self, client: BridgeClient) -> None:
        self._client = client

    async def _execute_bridge(self, params: BaseModel) -> ToolResult:
        try:
            call = await self._client.call(
                self.name,
                params.model_dump(exclude_none=True),
            )
        except BridgeError as error:
            return ToolResult(output=f"Tool bridge error: {error}", is_error=True)
        return ToolResult(output=json.dumps(call.result, ensure_ascii=False, separators=(",", ":")))


class RepoListTool(BridgeTool):
    name = "repo_list"
    description = (
        "List repository files and directories. Paths are relative to the repository; Git metadata is hidden."
    )
    params_model = RepoListParams
    category = "read"

    async def execute(self, params: RepoListParams) -> ToolResult:
        return await self._execute_bridge(params)


class RepoReadTool(BridgeTool):
    name = "repo_read"
    description = "Read a line-numbered excerpt from a repository source, test, configuration, or documentation file."
    params_model = RepoReadParams
    category = "read"

    async def execute(self, params: RepoReadParams) -> ToolResult:
        return await self._execute_bridge(params)


class RepoSearchTool(BridgeTool):
    name = "repo_search"
    description = "Case-insensitive literal search across Python, configuration, test, and documentation files."
    params_model = RepoSearchParams
    category = "read"

    async def execute(self, params: RepoSearchParams) -> ToolResult:
        return await self._execute_bridge(params)


class RepoWriteTool(BridgeTool):
    name = "repo_write"
    description = (
        "Create or replace an authorized repository file. Product source and new tests are writable; existing tests, "
        "lock files, Git metadata, and evaluator assets are protected."
    )
    params_model = RepoWriteParams
    category = "write"

    async def execute(self, params: RepoWriteParams) -> ToolResult:
        return await self._execute_bridge(params)


class RepoReplaceTool(BridgeTool):
    name = "repo_replace"
    description = (
        "Replace an exact text fragment in an authorized repository file. The operation fails unless the expected "
        "occurrence count matches."
    )
    params_model = RepoReplaceParams
    category = "write"

    async def execute(self, params: RepoReplaceParams) -> ToolResult:
        return await self._execute_bridge(params)


class RepoDeleteTool(BridgeTool):
    name = "repo_delete"
    description = (
        "Delete an untracked file created during this attempt. Tracked files, directories, lock files, Git metadata, "
        "and paths outside the repository cannot be deleted."
    )
    params_model = RepoDeleteParams
    category = "write"

    async def execute(self, params: RepoDeleteParams) -> ToolResult:
        return await self._execute_bridge(params)


class RepoDiffTool(BridgeTool):
    name = "repo_diff"
    description = "Show the current repository status and tracked diff for changes made during this attempt."
    params_model = RepoDiffParams
    category = "read"

    async def execute(self, params: RepoDiffParams) -> ToolResult:
        return await self._execute_bridge(params)


class CommandRunTool(BridgeTool):
    name = "command_run"
    description = (
        "Run a bounded Python or pytest command from the repository root. Supply an argv array; shell syntax, inline "
        "Python, network tools, and protected paths are unavailable."
    )
    params_model = CommandRunParams
    category = "command"

    async def execute(self, params: CommandRunParams) -> ToolResult:
        return await self._execute_bridge(params)


def create_eval_registry(client: BridgeClient) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        RepoListTool(client),
        RepoReadTool(client),
        RepoSearchTool(client),
        RepoWriteTool(client),
        RepoReplaceTool(client),
        RepoDeleteTool(client),
        RepoDiffTool(client),
        CommandRunTool(client),
    ):
        registry.register(tool)
    return registry
