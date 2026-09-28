from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from nanocursor.config import MCPServerConfig, build_child_env, resolve_env_vars

logger = logging.getLogger(__name__)


class MCPClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._alive = False
        # 存储 MCP 服务器的 InitializeResult，用于提取 instructions 等元信息
        self._init_result: types.InitializeResult | None = None
        self.work_dir: str | None = None
        self._owner: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._stop = asyncio.Event()
        self._close_error: BaseException | None = None


    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def instructions(self) -> str:
        """返回 MCP 服务器的 instructions（来自 InitializeResult）。"""
        if self._init_result is not None and self._init_result.instructions:
            return self._init_result.instructions
        return ""


    async def connect(self) -> None:
        if self._alive:
            return
        if self._owner is None or self._owner.done():
            self._ready = asyncio.get_running_loop().create_future()
            self._stop = asyncio.Event()
            self._close_error = None
            self._owner = asyncio.create_task(self._serve(), name=f"mcp:{self.name}")
        try:
            await asyncio.shield(self._ready)
        except BaseException:
            await self.close()
            raise

    async def _serve(self) -> None:
        # MCP's AnyIO task groups must enter and exit in the SAME task. Tool
        # calls and shutdown can come from different Agent/UI tasks.
        try:
            async with AsyncExitStack() as stack:
                self._stack = stack
                if self.config.is_stdio:
                    read, write = await self._connect_stdio()
                else:
                    read, write = await self._connect_http()
                self._session = await stack.enter_async_context(ClientSession(read, write))
                self._init_result = await self._session.initialize()
                self._alive = True
                self._ready.set_result(None)
                await self._stop.wait()
        except BaseException as exc:
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(exc)
                # The connecting caller may already have been cancelled.
                self._ready.exception()
            elif not isinstance(exc, asyncio.CancelledError):
                # The owner task cannot propagate an exception into the task
                # that requested shutdown. Keep it until close() can report
                # that transport cleanup was not confirmed.
                self._close_error = exc
                logger.debug("MCP connection closed: %s", self.name, exc_info=True)
        finally:
            self._alive = False
            self._session = None
            self._stack = None


    async def _connect_stdio(self) -> tuple[Any, Any]:
        assert self._stack is not None
        assert self.config.command is not None

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=build_child_env(self.config.env),
            cwd=self.work_dir,
        )
        devnull = open(os.devnull, "w")
        self._stack.callback(devnull.close)
        read, write = await self._stack.enter_async_context(
            stdio_client(params, errlog=devnull)
        )
        return read, write

    async def _connect_http(self) -> tuple[Any, Any]:
        assert self._stack is not None
        assert self.config.url is not None

        resolved_headers = {
            k: resolve_env_vars(v) for k, v in self.config.headers.items()
        }
        http_client = httpx.AsyncClient(
            headers=resolved_headers,
            follow_redirects=True,
        )
        await self._stack.enter_async_context(http_client)

        result = await self._stack.enter_async_context(
            streamable_http_client(self.config.url, http_client=http_client)
        )
        read, write = result[0], result[1]
        return read, write


    async def list_tools(self) -> list[types.Tool]:
        if not self.is_alive or self._session is None:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        result = await self._session.list_tools()
        return list(result.tools)


    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        if not self.is_alive or self._session is None:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        return await self._session.call_tool(name, arguments)

    async def close(self) -> None:
        self._alive = False
        self._stop.set()
        owner = self._owner
        if owner is not None:
            if self._ready is not None and not self._ready.done():
                owner.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(owner), timeout=5)
            except TimeoutError:
                owner.cancel()
                await asyncio.wait_for(asyncio.shield(owner), timeout=5)
            except asyncio.CancelledError:
                # Finish owned transport cleanup before propagating user stop.
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
                raise
            self._owner = None
            if self._close_error is not None:
                raise self._close_error
