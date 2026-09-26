from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from rich.text import Text
from textual.app import App
from textual.widgets import Static

from nanocursor.agent import PermissionRequest, PermissionResponse
from nanocursor.app import NanoCursorApp
from nanocursor.memory.session import SessionManager
from nanocursor.permission_dialog import InlinePermissionWidget
from test_auto_approval import setup  # Shared isolated agent/probe fixture; no real commands.


@pytest.mark.asyncio
async def test_once_widget_renders_literal_command_and_keyboard_denial():
    responses = []
    class DialogApp(App):
        def compose(self):
            yield InlinePermissionWidget("Bash", "echo '[bold]x[/bold]'", reason="[link=evil]理由[/link]",
                                         cwd="/project", allow_always=False)
        def on_inline_permission_widget_responded(self, event):
            responses.append(event.response)
    app = DialogApp()
    async with app.run_test() as pilot:
        widget = app.query_one(InlinePermissionWidget)
        content = widget._build_content()
        assert isinstance(content, Text)
        assert "[bold]x[/bold]" in content.plain
        assert "[link=evil]理由[/link]" in content.plain
        assert len(widget._options) == 2
        await pilot.press("down", "enter")
        assert responses == [PermissionResponse.DENY]


def make_app(s):
    p = s.controller.main
    app = NanoCursorApp([p, replace(p, name="other")])  # No automatic provider selection/network init.
    app.agent = s.agent
    app.registry = s.agent.registry
    app._selected_provider = p
    app.session_manager = SessionManager(str(s.root))
    app.session = app.session_manager.create()
    app.agent.session_id = app.session.session_id
    app.agent.approval_controller.on_authorization_changed = app._save_approval_context
    app.agent.approval_controller.on_status = app._show_approval_status
    app.agent.on_permission_mode_changed = app._update_mode_label
    return app


@pytest.mark.asyncio
async def test_main_tui_toggle_prompt_and_context_provenance(setup, monkeypatch):
    s = setup
    app = make_app(s)
    monkeypatch.setattr("nanocursor.app.expand_at_refs", lambda text, cwd: text + "\nUNTRUSTED FILE BODY")
    async with app.run_test() as pilot:
        await app._dispatch_command("/approval off")
        assert s.controller.config.mode == "manual"
        await app._dispatch_command("/approval on")
        assert s.controller.config.mode == "smart"
        await app._dispatch_command("/approval provider main")
        await app._send_message("查看 @file", direct_input="查看 @file")
        records = s.controller.authorization.records
        assert records[-1]["text"] == "查看 @file"
        assert "UNTRUSTED" not in str(records)
        before = len(records)
        await app._send_message("内部系统通知", is_notification=True)
        assert len(records) == before
        await app._send_message("Skill expanded prompt")
        assert len(records) == before
        future = asyncio.get_running_loop().create_future()
        await app._handle_permission_request(PermissionRequest("Bash", "git push", future,
                                                               "需要人工确认", str(s.root), False))
        await pilot.pause()
        widget = app.query_one(InlinePermissionWidget)
        assert len(widget._options) == 2
        widget.action_select()
        await pilot.pause()
        assert future.result() == PermissionResponse.ALLOW
        old_id = app.session.session_id
        await app._dispatch_command("/clear")
        assert app.session.session_id != old_id
        assert not s.controller.authorization.records
        assert s.controller.authorization.complete


@pytest.mark.asyncio
async def test_session_switch_resets_authorization_not_conversation_roles(setup):
    s = setup
    app = make_app(s)
    old = app.session
    s.controller.persist_authorization()
    new = app.session_manager.create()
    app._set_session(new)
    assert not s.controller.authorization.records
    assert s.controller.authorization.complete
    app._set_session(old)
    assert "不要推送" in s.controller.authorization.records[0]["text"]
    new.close()
    old.close()


@pytest.mark.asyncio
async def test_file_approval_enables_edit_mode_and_updates_tui(setup):
    from nanocursor.conversation import ConversationManager
    from nanocursor.permissions import PermissionMode
    from nanocursor.tools.base import StreamEnd, ToolCallComplete, TextDelta
    from nanocursor.tools.write_file import WriteFile
    from nanocursor.tools.edit_file import EditFile
    from test_permissions import MockLLMClient

    s = setup
    s.agent.registry.register(WriteFile())
    s.agent.registry.register(EditFile())
    target = s.root / "example.txt"
    s.agent.client = MockLLMClient([
        [ToolCallComplete("write", "WriteFile", {"file_path": str(target), "content": "before"}),
         ToolCallComplete("edit", "EditFile", {"file_path": str(target), "old_string": "before", "new_string": "after"}),
         StreamEnd("tool_use")],
        [TextDelta("done"), StreamEnd("end_turn")],
    ])
    app = make_app(s)
    shown = asyncio.Event()
    requests = []
    async def run():
        conversation = ConversationManager()
        conversation.add_user_message("Create and edit a file")
        async for event in s.agent.run(conversation):
            if isinstance(event, PermissionRequest):
                requests.append(event)
                await app._handle_permission_request(event)
                shown.set()
    async with app.run_test() as pilot:
        task = asyncio.create_task(run())
        try:
            await asyncio.wait_for(shown.wait(), 5)
            await pilot.pause()
            widget = app.query_one(InlinePermissionWidget)
            assert requests[0].allow_edits
            assert widget._options[1][1] == PermissionResponse.ALLOW_EDITS
            await pilot.press("down", "enter")
            await asyncio.wait_for(task, 5)
            await pilot.pause()
            assert len(requests) == 1
            assert target.read_text() == "after"
            assert s.agent.permission_mode == PermissionMode.ACCEPT_EDITS
            assert s.checker.mode == PermissionMode.ACCEPT_EDITS
            assert "accept-edits" in str(app.query_one("#mode-label", Static).render())
            assert not s.checker._session_allowed
            assert not (s.root / "permissions.yaml").exists()
            assert s.checker.check(s.bash, {"command": "python -m pytest"}).effect == "ask"
            await app._dispatch_command("/permission mode default")
            assert s.agent.permission_mode == PermissionMode.DEFAULT
            assert s.checker.check(s.agent.registry.get("WriteFile"), {"file_path": str(target)}).effect == "ask"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_edit_mode_option_is_scoped_to_eligible_file_requests():
    for name in ("WriteFile", "EditFile"):
        widget = InlinePermissionWidget(name, "file.txt", allow_edits=True)
        assert PermissionResponse.ALLOW_EDITS in [response for _, response in widget._options]
        assert PermissionResponse.ALLOW_EDITS not in [response for _, response in InlinePermissionWidget(name, "file.txt")._options]
    widget = InlinePermissionWidget("Bash", "git push", allow_always=False, allow_edits=True)
    assert [response for _, response in widget._options] == [PermissionResponse.ALLOW, PermissionResponse.DENY]
