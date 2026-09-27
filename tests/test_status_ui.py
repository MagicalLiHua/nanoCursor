"""Terminal status behavior, using local fake clients and no model API calls."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from rich.cells import cell_len
from textual.app import App
from textual.widgets import Static

from nanocursor.agent import (
    CompactNotification,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
)
from nanocursor.app import ChatInput, ToolCallBlock, ToolGroupSummary
from nanocursor.conversation import Message
from nanocursor.permissions import PermissionMode
from nanocursor.permission_dialog import InlinePermissionWidget
from nanocursor.status import StatusSnapshot
from nanocursor.status_bar import StatusBar, StatusDetailsScreen
from nanocursor.tools.base import StreamEnd, TextDelta
from test_approval_ui import make_app
from test_auto_approval import setup  # Isolated fake provider/agent fixture.
from test_permissions import MockLLMClient
from test_stream_usage import chunk, client_for, usage


def show_chat(app):
    """Bypass provider selection without initializing any network client."""
    app.query_one("#provider-select").display = False
    app.query_one("#chat-area").display = True
    app.query_one("#input-area").display = True
    app.query_one("#chat-input", ChatInput).focus()
    app._update_mode_label()


def assert_two_visible_lines(app):
    bar = app.query_one(StatusBar)
    labels = [bar.query_one(f"#{name}", Static) for name in ("model-label", "mode-label")]
    assert bar.content_size.height == 2
    for label in labels:
        rendered = str(label.render())
        assert rendered
        assert "\n" not in rendered
        assert cell_len(rendered) <= label.content_size.width
        assert label.region.height == 1
        assert label.region.x >= 0
        assert label.region.right <= app.size.width
        assert label.region.y >= 0
        assert label.region.bottom <= app.size.height
    return labels


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 32), (90, 30), (80, 24), (50, 20)])
async def test_status_widget_fits_two_lines_and_keeps_model_markup_literal(size):
    model = "[bold]模型[/bold]-" + "very-long-model-name-" * 30
    snapshot = StatusSnapshot(
        model=model, reasoning="开启", context_used=24_500, context_window=128_000,
        input_tokens=134_000, output_tokens=12_500, permission_mode="accept-edits",
        approval="smart", sandbox="关闭", mcp_connected=2, mcp_configured=3,
        tools_enabled=35, tools_visible=20, tools_builtin=15, tools_mcp=20,
        approval_notice="正在审查一个很长的命令" * 30,
    )

    class StatusApp(App):
        CSS_PATH = Path(__file__).resolve().parents[1] / "nanocursor" / "styles.tcss"

        def compose(self):
            yield StatusBar(id="status-bar")

        def on_mount(self):
            self.query_one(StatusBar).set_snapshot(snapshot)

    app = StatusApp()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        model_label, _ = assert_two_visible_lines(app)
        assert "[bold]" in str(model_label.render())
        assert "模型" in str(model_label.render())


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 32), (90, 30), (80, 24), (50, 20)])
async def test_main_app_keeps_status_and_input_on_screen(setup, size):
    app = make_app(setup)
    app._selected_provider = replace(app._selected_provider, model="[bold]模型[/bold]-" + "x" * 200)
    async with app.run_test(size=size) as pilot:
        show_chat(app)
        await pilot.pause()
        # Do not shadow App.theme's reactive descriptor: that silently keeps
        # Textual's fallback colors even when current_theme reports our theme.
        assert app.screen.styles.background.hex == "#171A1F"
        assert_two_visible_lines(app)
        input_widget = app.query_one("#chat-input", ChatInput)
        assert input_widget.region.height > 0
        assert input_widget.region.bottom <= app.size.height


@pytest.mark.asyncio
async def test_greeting_sdk_chunks_update_tokens_and_keep_input_compact(setup):
    """Exercise Compat parsing before Agent/UI accounting, without HTTP."""
    app = make_app(setup)
    greeting = "你好！我是你的编程助手，可以帮你阅读代码、修复问题或实现新功能。"
    setup.agent.client = client_for([
        chunk(content=greeting),
        chunk(finish="stop", usage=usage(prompt=1040, output=50, prompt_cache_hit_tokens=0)),
    ])
    setup.agent.context_window = 128_000
    async with app.run_test(size=(90, 30)) as pilot:
        show_chat(app)
        await app._send_message("你好")
        await pilot.pause()
        bar = app.query_one(StatusBar)
        assert (bar.snapshot.input_tokens, bar.snapshot.output_tokens) == (1040, 50)
        assert bar.snapshot.context_used == 1090
        model_line, mode_line = assert_two_visible_lines(app)
        assert "<1%" in str(model_line.render())
        assert "↑50" in str(mode_line.render())
        assert app.query_one("#input-area").region.height <= 5
        assert str(app.query_one("MarkdownParagraph").render()) == greeting


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(90, 30), (80, 24), (50, 20)])
async def test_multiline_input_grows_then_scrolls_without_hiding_status(setup, size):
    app = make_app(setup)
    async with app.run_test(size=size) as pilot:
        show_chat(app)
        input_widget = app.query_one("#chat-input", ChatInput)
        for line_count in (3, 30):
            input_widget.load_text("\n".join(f"第 {index} 行" for index in range(line_count)))
            await pilot.pause()
            assert_two_visible_lines(app)
            bar = app.query_one(StatusBar)
            assert 3 <= input_widget.region.height <= 10
            assert input_widget.region.bottom <= bar.region.y
            assert bar.region.bottom <= app.size.height
            assert app.query_one("#chat-area").region.height >= 3


@pytest.mark.asyncio
async def test_approval_notice_does_not_replace_model_and_mode_changes_refresh(setup):
    app = make_app(setup)
    async with app.run_test(size=(120, 32)) as pilot:
        show_chat(app)
        await pilot.pause()
        model_label = app.query_one("#model-label", Static)
        initial_model = str(model_label.render())
        app._show_approval_status("正在检查 [bold]echo test[/bold]")
        await pilot.pause()
        assert str(model_label.render()) == initial_model

        await app._dispatch_command("/permission mode acceptEdits")
        await pilot.pause()
        assert setup.agent.permission_mode == PermissionMode.ACCEPT_EDITS
        assert "accept-edits" in str(app.query_one("#mode-label", Static).render())
        assert str(model_label.render()) == initial_model


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["keyboard", "click"])
async def test_status_details_escape_restores_input_without_cancelling_task(setup, entry):
    app = make_app(setup)
    app._selected_provider = replace(app._selected_provider, model="[bold]detail-model[/bold]")
    waiting_task = asyncio.create_task(asyncio.Event().wait())
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            show_chat(app)
            app._streaming = True
            app._agent_task = waiting_task
            await pilot.pause()
            if entry == "keyboard":
                await pilot.press("f2")
            else:
                await pilot.click("#status-bar")
            await pilot.pause()
            assert isinstance(app.screen, StatusDetailsScreen)
            details = "\n".join(str(widget.render()) for widget in app.screen.query(Static))
            assert "[bold]detail-model[/bold]" in details
            assert "MCP" in details
            assert "secret" not in details
            setup.agent.total_input_tokens = 4321
            app.refresh_status()
            await pilot.pause()
            details = "\n".join(str(widget.render()) for widget in app.screen.query(Static))
            assert "4,321" in details
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, StatusDetailsScreen)
            assert app.query_one("#chat-input", ChatInput).has_focus
            assert not waiting_task.done()
            assert app._streaming
            app._agent_task = None
            app._streaming = False
    finally:
        waiting_task.cancel()
        await asyncio.gather(waiting_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_approval", [False, True])
async def test_status_details_keys_do_not_change_background_permissions_or_tools(setup, pending_approval):
    app = make_app(setup)
    async with app.run_test(size=(80, 24)) as pilot:
        show_chat(app)
        block = ToolCallBlock("Bash", {"command": "echo example"})
        block.set_result("example", False, 0.1)
        await app.query_one("#chat-area").mount(block)
        approval_future = None
        if pending_approval:
            approval_future = asyncio.get_running_loop().create_future()
            await app._handle_permission_request(PermissionRequest("Bash", "echo example", approval_future))
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StatusDetailsScreen)
        chain = screen.focus_chain
        expected_focus = chain[(chain.index(screen.focused) - 1) % len(chain)]

        await pilot.press("shift+tab", "ctrl+o")
        await pilot.pause()
        assert screen.focused is expected_focus
        assert setup.agent.permission_mode == PermissionMode.DEFAULT
        assert block._collapsed
        if approval_future is not None:
            assert not approval_future.done()

        await pilot.press("f2")
        await pilot.pause()
        if pending_approval:
            assert app.query_one(InlinePermissionWidget).has_focus
            assert not approval_future.done()
            await pilot.press("down", "down", "enter")
            await pilot.pause()
            assert approval_future.result() == PermissionResponse.DENY
        else:
            assert app.query_one("#chat-input", ChatInput).has_focus
            await pilot.press("shift+tab", "ctrl+o")
            await pilot.pause()
            assert setup.agent.permission_mode == PermissionMode.ACCEPT_EDITS
            assert not block._collapsed


@pytest.mark.asyncio
async def test_completed_turn_and_compaction_refresh_context_separately_from_usage(setup, monkeypatch):
    from unittest.mock import AsyncMock
    # Background summaries now have a real owned lifecycle; isolate their
    # independent request from the scripted foreground usage responses.
    monkeypatch.setattr("nanocursor.app.generate_session_summary", AsyncMock(return_value=""))
    app = make_app(setup)
    setup.agent.client = MockLLMClient([
        [TextDelta("First response"), StreamEnd("end_turn", input_tokens=1200, output_tokens=100)],
        [TextDelta("Second response"), StreamEnd("end_turn", input_tokens=300, output_tokens=50)],
    ])
    snapshots = []
    original = StatusBar.set_snapshot

    def observe_status(bar, snapshot):
        snapshots.append(snapshot)
        return original(bar, snapshot)

    monkeypatch.setattr(StatusBar, "set_snapshot", observe_status)
    async with app.run_test(size=(120, 32)) as pilot:
        show_chat(app)
        await app._send_message("First task")
        await pilot.pause()
        assert snapshots[-1].context_used == 1300
        assert snapshots[-1].input_tokens == 1200
        assert snapshots[-1].output_tokens == 100

        await app._send_message("Second task")
        await pilot.pause()
        completed = snapshots[-1]
        assert completed.context_used == 350
        assert completed.input_tokens == 1500
        assert completed.output_tokens == 150

        async def compacted_run(conversation, **kwargs):
            conversation.replace_history([Message(role="user", content="Compacted summary.")])
            yield CompactNotification(before_tokens=350, message="Compacted")
            yield LoopComplete(total_turns=0)

        monkeypatch.setattr(setup.agent, "run", compacted_run)
        await app._send_message("")
        await pilot.pause()
        compacted = snapshots[-1]
        assert compacted.context_used == app.conversation.current_tokens()
        assert compacted.context_used < completed.context_used
        assert compacted.input_tokens == completed.input_tokens
        assert compacted.output_tokens == completed.output_tokens
        assert compacted.context_source != completed.context_source


@pytest.mark.asyncio
async def test_group_summary_expands_read_blocks_and_never_hides_errors(setup, monkeypatch):
    app = make_app(setup)

    async def read_results(conversation, **kwargs):
        for tool_id, filename, failed in (
            ("read-1", "one.py", False),
            ("read-2", "two.py", False),
            ("read-3", "missing.py", True),
        ):
            yield ToolUseEvent(tool_name="ReadFile", tool_id=tool_id, arguments={"file_path": filename})
            yield ToolResultEvent(tool_id=tool_id, tool_name="ReadFile",
                                  output="File missing" if failed else "Contents", is_error=failed, elapsed=0.1)
        yield TurnComplete(turn=1)
        yield LoopComplete(total_turns=1)

    monkeypatch.setattr(setup.agent, "run", read_results)
    async with app.run_test(size=(120, 32)) as pilot:
        show_chat(app)
        await app._send_message("Read three files")
        await pilot.pause()
        blocks = list(app.query(ToolCallBlock))
        reads = [block for block in blocks if not block._is_error]
        error = next(block for block in blocks if block._is_error)
        assert len(reads) == 2
        assert all(not block.display for block in reads)
        assert error.display
        assert "File missing" in str(error.render())
        summary = app.query_one(ToolGroupSummary)
        assert summary._count == 2

        await pilot.click(summary)
        await pilot.pause()
        assert all(block.display for block in reads)
        assert error.display
        await pilot.click(summary)
        await pilot.pause()
        assert all(not block.display for block in reads)
        assert error.display

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(block.display for block in reads)
        assert error.display
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(not block.display for block in reads)
        assert error.display
