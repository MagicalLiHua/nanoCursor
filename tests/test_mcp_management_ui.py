from __future__ import annotations

import asyncio

import pytest

from nanocursor.agent import PermissionResponse
from nanocursor.permission_dialog import InlinePermissionWidget
from nanocursor.status import collect_status
from nanocursor.tools.base import StreamEnd, TextDelta, ToolCallComplete
from test_memory_ui import memory_app, command
from test_mcp_management import FakeClient


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(90, 30), (80, 24)])
async def test_model_can_connect_discover_call_and_stop_in_interactive_ui(memory_app, monkeypatch, size):
    make, main, _ = memory_app
    clients, schemas = [], []
    def create(config):
        client = FakeClient(config)
        clients.append(client)
        return client
    monkeypatch.setattr("nanocursor.mcp.manager.MCPClient", create)
    batches = [
        [ToolCallComplete("start", "ManageMCP", {"action": "start", "name": "docs", "config": {"url": "https://docs.example/mcp"}}), StreamEnd("tool_use")],
        [ToolCallComplete("find", "ToolSearch", {"query": "select:mcp_docs_search"}), StreamEnd("tool_use")],
        [ToolCallComplete("call", "mcp_docs_search", {}), StreamEnd("tool_use")],
        [TextDelta("CONNECTED"), StreamEnd("end_turn")],
        [ToolCallComplete("stop", "ManageMCP", {"action": "stop", "name": "docs"}), StreamEnd("tool_use")],
        [TextDelta("STOPPED"), StreamEnd("end_turn")],
    ]
    original = main.stream
    async def stream(conversation, system="", tools=None, **kwargs):
        if tools is not None and batches:
            schemas.append([schema["name"] for schema in tools])
            for event in batches.pop(0):
                yield event
        else:
            async for event in original(conversation, system, tools, **kwargs):
                yield event
    monkeypatch.setattr(main, "stream", stream)
    app = make()
    async with app.run_test(size=size) as pilot:
        assert app.command_registry.find("tools") is not None
        await command(app, "连接文档 MCP 并查询")
        approvals = []
        for _ in range(100):
            await pilot.pause(0.01)
            dialogs = app.query(InlinePermissionWidget)
            request = getattr(app, "_pending_perm_request", None)
            if dialogs and request is not None and not request.future.done():
                dialog = dialogs.first()
                assert dialog._tool_name == request.tool_name
                if dialog._tool_name == "ManageMCP":
                    assert len(dialog._options) == 2
                    assert "https://docs.example/mcp" in dialog._description
                # Mount/focus and DOM removal are asynchronous. Count the
                # resolved request, not how often a widget is still visible.
                dialog.focus()
                await pilot.press("enter")
                assert await asyncio.wait_for(asyncio.shield(request.future), 2) == PermissionResponse.ALLOW
                approvals.append(request.tool_name)
            if app._agent_task is None or app._agent_task.done():
                break
        assert app._agent_task is None or app._agent_task.done()
        assert approvals == ["ManageMCP", "mcp_docs_search"]
        assert collect_status(app).mcp_connected == 1
        assert "mcp_docs_search" in schemas[2]
        await command(app, "/tools enabled")
        text = "\n".join(str(widget.render()) for widget in app.query("#chat-area Static"))
        assert "mcp_docs_search" in text and "已启用" in text
        await command(app, "关闭文档 MCP")
        stop_approvals = []
        for _ in range(100):
            await pilot.pause(0.01)
            dialogs = app.query(InlinePermissionWidget)
            request = getattr(app, "_pending_perm_request", None)
            if dialogs and request is not None and not request.future.done():
                dialog = dialogs.first()
                assert dialog._tool_name == request.tool_name
                dialog.focus()
                await pilot.press("enter")
                assert await asyncio.wait_for(asyncio.shield(request.future), 2) == PermissionResponse.ALLOW
                stop_approvals.append(request.tool_name)
            if app._agent_task is None or app._agent_task.done():
                break
        assert app._agent_task is None or app._agent_task.done()
        assert stop_approvals == ["ManageMCP"]
        assert not batches and clients[0].closed
        assert collect_status(app).mcp_connected == 0
        assert "mcp_docs_search" not in schemas[-1]
        assert "docs" not in app._mcp_instructions
        await command(app, "/mcp")
        text = "\n".join(str(widget.render()) for widget in app.query("#chat-area Static"))
        assert "docs: 已关闭" in text
    restarted = make()
    async with restarted.run_test(size=size):
        assert len(clients) == 1
        assert collect_status(restarted).mcp_configured == 1
        assert collect_status(restarted).mcp_connected == 0
        assert "docs" in restarted.mcp_manager._disabled
