from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable

from nanocursor.config import MCPServerConfig
from nanocursor.mcp.client import MCPClient
from nanocursor.mcp.tool_wrapper import MCPToolWrapper
from nanocursor.tools import ToolRegistry
from nanocursor.tools.base import Tool

@dataclass
class ServerInfo:
    name: str
    instructions: str = ""


@dataclass
class ConnectResult:
    tools: list[Tool] = field(default_factory=list)
    servers: list[ServerInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class MCPManager:
    def __init__(self, *, work_dir: str | None = None,
                 on_change: Callable[[], None] | None = None) -> None:
        self._configs: dict[str, MCPServerConfig] = {}
        self._clients: dict[str, MCPClient] = {}
        self._disabled: set[str] = set()
        self._tools: dict[str, list[Tool]] = {}
        self._registry: ToolRegistry | None = None
        self._lock = asyncio.Lock()
        self._closing = False
        self.work_dir = work_dir
        self.on_change = on_change
        self.connect_timeout = 30.0

    def load_configs(self, configs: list[MCPServerConfig]) -> None:
        for cfg in configs:
            self._configs[cfg.name] = deepcopy(cfg)

    def remember_disabled(self, config: MCPServerConfig) -> None:
        self._configs[config.name] = deepcopy(config)
        self._disabled.add(config.name)

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    def instructions(self) -> str:
        parts = []
        for name, client in self._clients.items():
            if not client.is_alive or name in self._disabled:
                continue
            detail = client.instructions
            if not isinstance(detail, str) or not detail:
                detail = "Available tools: " + ", ".join(t.name for t in self._tools.get(name, []))
            parts.append(f"## {name}\n{detail[:12000]}")
        return ("# MCP Server Instructions\n\n" + "\n\n".join(parts))[:24000] if parts else ""

    async def start(self, config: MCPServerConfig, registry: ToolRegistry | None = None) -> ConnectResult:
        async with self._lock:
            if self._closing:
                raise RuntimeError("MCP manager is shutting down")
            if registry is not None:
                self._registry = registry
            await self._stop(config.name)
            self.remember_disabled(config)
            client = MCPClient(deepcopy(config))
            client.work_dir = self.work_dir
            self._clients[config.name] = client
            try:
                async with asyncio.timeout(self.connect_timeout):
                    await client.connect()
                    definitions = await client.list_tools()
                wrappers = [MCPToolWrapper(config.name, definition, client) for definition in definitions]
                names = [tool.name for tool in wrappers]
                if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) for name in names):
                    raise ValueError("MCP tool names must fit the model's 64-character letters/digits/underscore/hyphen format; use a shorter server name")
                if len(names) != len(set(names)):
                    raise ValueError("MCP server returned duplicate tool names")
                if self._registry is not None:
                    for name in names:
                        if self._registry.get(name) is not None:
                            raise ValueError(f"MCP tool name collision: {name}")
                    for wrapper in wrappers:
                        self._registry.register(wrapper)
                self._tools[config.name] = wrappers
                self._disabled.discard(config.name)
                self._changed()
                return ConnectResult(wrappers, [ServerInfo(config.name, client.instructions)])
            except BaseException:
                await self._stop(config.name)
                raise

    async def _stop(self, name: str) -> None:
        self._disabled.add(name)
        # Withdraw schemas BEFORE awaiting transport cleanup. Old child scopes
        # retain this client identity, whose close() revokes calls immediately.
        for tool in self._tools.pop(name, []):
            if self._registry is not None and self._registry.get(tool.name) is tool:
                self._registry.unregister(tool.name)
        client = self._clients.get(name)
        try:
            if client is not None:
                await client.close()
                self._clients.pop(name, None)
        finally:
            self._changed()

    async def stop(self, name: str) -> None:
        async with self._lock:
            await self._stop(name)

    async def connect_all(self) -> ConnectResult:
        result = ConnectResult()
        for name, config in list(self._configs.items()):
            if name in self._disabled:
                continue
            try:
                connected = await self.start(config)
                result.tools.extend(connected.tools)
                result.servers.extend(connected.servers)
            except Exception as exc:
                # Startup failure is not an explicit user disable.
                self._disabled.discard(name)
                result.errors.append(f"MCP server '{name}': {exc}")
        return result

    async def register_all_tools(self, registry: ToolRegistry) -> ConnectResult:
        self._registry = registry
        return await self.connect_all()

    async def get_client(self, name: str) -> MCPClient | None:
        """Look up a live connection; never restart a user-stopped process."""
        client = self._clients.get(name)
        return client if client is not None and client.is_alive and name not in self._disabled else None

    async def shutdown(self) -> None:
        self._closing = True
        async with self._lock:
            failures = []
            for name in list(self._clients):
                try:
                    await self._stop(name)
                except Exception:
                    failures.append(name)
            if failures:
                raise RuntimeError("MCP shutdown incomplete: " + ", ".join(failures))
