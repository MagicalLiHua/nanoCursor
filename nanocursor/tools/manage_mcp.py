from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from nanocursor.config import MCPServerConfig
from nanocursor.mcp.manager import MCPManager
from nanocursor.mcp.settings import MCPSettings, check_project_override
from nanocursor.runtime import redact
from nanocursor.tools import ToolRegistry
from nanocursor.tools.base import Tool, ToolResult
from nanocursor.tools.runtime import current_runtime


class MCPDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command: str | None = Field(default=None, min_length=1)
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict, description="Explicit process environment; use ${ENV_VAR} for secrets")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP headers; use ${ENV_VAR} for secrets")

    @model_validator(mode="after")
    def validate_transport(self):
        if (self.command is None) == (self.url is None):
            raise ValueError("Provide exactly one of command (stdio) or url (Streamable HTTP)")
        if self.command and (not self.command.strip() or "\x00" in self.command):
            raise ValueError("command must be a nonempty executable name")
        if self.url is not None:
            address = urlsplit(self.url)
            if address.scheme not in {"http", "https"} or not address.hostname or address.username or address.password:
                raise ValueError("url must be an HTTP(S) endpoint without embedded credentials")
            if self.args or self.env:
                raise ValueError("args/env belong to stdio servers; use headers for HTTP")
        elif self.headers:
            raise ValueError("headers belong to HTTP servers; use env for stdio")
        return self


class ManageMCPParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["list", "start", "stop"]
    name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    config: MCPDefinition | None = Field(default=None, description="For start: complete configuration for a new/replaced server. Omit to restart a saved server.")
    _snapshot: bytes | None = PrivateAttr(default=None)
    _entry: dict = PrivateAttr(default_factory=dict)
    _project_snapshots: tuple = PrivateAttr(default=())
    _cwd: str = PrivateAttr(default="")

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "list" and (self.name is not None or self.config is not None):
            raise ValueError("list takes no name or config")
        if self.action != "list" and not self.name:
            raise ValueError("start/stop require name")
        if self.action == "stop" and self.config is not None:
            raise ValueError("stop takes only name")
        return self


def config_entry(config: MCPServerConfig) -> dict:
    return {key: value for key, value in asdict(config).items() if value is not None}


class ManageMCP(Tool):
    name = "ManageMCP"
    description = (
        "Manage MCP servers in the interactive MAIN session. action=list reads local status. "
        "action=start saves a new/replacement config and connects now, or restarts a saved server by name. "
        "action=stop disconnects and saves enabled:false, so it stays off on restart. "
        "Changes persist in the user config for other projects. Project overrides require manual editing. "
        "stdio command is an executable with separate args (not a shell string); HTTP uses url and optional headers. "
        "Use ${ENV_VAR} references for credentials. Local MCP processes run outside the Bash sandbox. "
        "Starting may download/run software such as npx packages. Ask the user through normal permission approval. "
        "On failure/cancellation configuration stays disabled. Use ToolSearch to discover connected tools."
    )
    params_model = ManageMCPParams
    category = "command"
    is_system_tool = True
    allow_always = False

    def __init__(self, manager: MCPManager, registry: ToolRegistry, *, owner_id: str,
                 workspace: Path, settings: MCPSettings | None = None) -> None:
        self.manager = manager
        self.registry = registry
        self.owner_id = owner_id
        self.workspace = workspace
        self.settings = settings or MCPSettings()

    def validate_arguments(self, arguments: dict) -> ManageMCPParams:
        params = super().validate_arguments(arguments)
        if params.action == "list":
            return params
        raw, params._snapshot = self.settings.read()
        params._project_snapshots = check_project_override(self.workspace, params.name, starting=params.action == "start")
        entries = {entry["name"]: entry for entry in raw.get("mcp_servers") or []}
        entry = entries.get(params.name)
        if entry is None and params.name in self.manager._configs:
            entry = config_entry(self.manager._configs[params.name])
        if params.action == "start":
            if params.config is None:
                if entry is None:
                    raise ValueError("New MCP server requires config with command or url")
                params.config = MCPDefinition.model_validate({key: value for key, value in entry.items()
                                                             if key not in {"name", "enabled"}})
            params._entry = {"name": params.name, **params.config.model_dump(exclude_none=True)}
        else:
            if entry is None:
                raise ValueError(f"Unknown MCP server: {params.name}")
            params._entry = entry
        runtime = current_runtime()
        params._cwd = str(runtime.cwd) if runtime else self.manager.work_dir or ""
        return params

    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, ManageMCPParams)
        runtime = current_runtime()
        if runtime is None or runtime.agent_id != self.owner_id:
            return ToolResult("ManageMCP is only available in its owning main session", True)
        if params.action == "list":
            raw, _ = self.settings.read()
            names = sorted(set(self.manager._configs) | {entry["name"] for entry in raw.get("mcp_servers") or []})
            lines = ["MCP servers (local status; no network probe):"]
            entries = {entry["name"]: entry for entry in raw.get("mcp_servers") or []}
            for name in names:
                client = self.manager._clients.get(name)
                connected = bool(client and client.is_alive and name not in self.manager._disabled)
                saved = entries.get(name)
                state = "connected" if connected else "disabled" if (name in self.manager._disabled or saved and not saved.get("enabled", True)) else "disconnected"
                lines.append(f"- {name}: {state}; {len(self.manager._tools.get(name, []))} tools; "
                             + ("user config" if saved else "runtime/project config"))
            return ToolResult("\n".join(lines) if names else "No MCP servers configured. Use start with name and config.")
        if params._snapshot is None or params._cwd != str(runtime.cwd):
            return ToolResult("MCP request is unprepared or working directory changed; retry", True)
        current = check_project_override(self.workspace, params.name, starting=params.action == "start")
        if current != params._project_snapshots:
            return ToolResult("Project settings changed during approval; retry", True)
        # Persist disabled BEFORE any process/network side effect. Only a fully
        # connected, registered service gets its startup switch turned back on.
        snapshot = self.settings.save(params._entry, enabled=False, expected=params._snapshot)
        try:
            if params.action == "stop":
                await self.manager.stop(params.name)
                return ToolResult(f"MCP '{params.name}' stopped; tools removed. Saved enabled:false in {self.settings.path}. "
                                  "For a remote service this disconnects the client; it does not shut down the remote host.")
            config = MCPServerConfig(**params._entry)
            self.manager.work_dir = str(runtime.cwd)
            result = await self.manager.start(config, self.registry)
            self.settings.save(params._entry, enabled=True, expected=snapshot)
            tools = ", ".join(tool.name for tool in result.tools)
            instructions = result.servers[0].instructions
            return ToolResult(f"MCP '{params.name}' connected; {len(result.tools)} tools registered. "
                              f"Saved enabled:true in {self.settings.path}. Use ToolSearch to discover: {tools}\n"
                              + (f"Server instructions:\n{instructions[:12000]}" if isinstance(instructions, str) else ""))
        except BaseException as exc:
            await self.manager.stop(params.name)
            if not isinstance(exc, Exception):
                raise
            return ToolResult(f"MCP '{params.name}' did not start; connection closed. "
                              f"Configuration was saved disabled before connecting. {redact(str(exc))[:600]}", True)
