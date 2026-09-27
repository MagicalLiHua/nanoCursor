"""File completion from the real composer through isolated Agent execution."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from nanocursor.app import ChatInput
from nanocursor.client import LLMClient
from nanocursor.commands.completion import CompletionPopup
from nanocursor.commands.registry import Command, CommandRegistry, CommandType
from nanocursor.tools.base import StreamEnd, TextDelta
from nanocursor.workspace import WorkspaceContext
from test_approval_ui import make_app
from test_auto_approval import setup  # Fake provider/reviewer; never contacts a model.


class RecordingClient(LLMClient):
    def __init__(self):
        self.prompts = []
        self.called = asyncio.Event()

    async def stream(self, conversation, system="", tools=None):
        self.prompts.append(deepcopy(conversation.history))
        self.called.set()
        yield TextDelta("已读取。")
        yield StreamEnd("end_turn", input_tokens=100, output_tokens=10)


@pytest.fixture
def file_app(setup, monkeypatch):
    root = setup.root
    (root / "alpha.py").write_text("ATTACHMENT_ONLY_ALPHA\n", encoding="utf-8")
    (root / "beta.py").write_text("ATTACHMENT_ONLY_BETA\n", encoding="utf-8")
    (root / "docs notes").mkdir()
    (root / "docs notes" / "a.md").write_text("ATTACHMENT_ONLY_QUOTED\n", encoding="utf-8")
    app = make_app(setup)
    app.workspace = WorkspaceContext.resolve(str(root))
    app.client = setup.agent.client = RecordingClient()
    # Session title generation is an unrelated extra model call. Preserve the
    # real task and authorization path while isolating what this client records.
    monkeypatch.setattr(app, "_update_session_summary", AsyncMock())
    return app


def show_chat(app):
    app.query_one("#provider-select").display = False
    app.query_one("#chat-area").display = True
    app.query_one("#input-area").display = True
    app.query_one("#chat-input", ChatInput).focus()


async def type_input(app, pilot, text, *, cursor=None):
    widget = app.query_one("#chat-input", ChatInput)
    widget.load_text(text)
    widget.move_cursor(widget.document.get_location_from_index(len(text) if cursor is None else cursor))
    widget.focus()
    await pilot.pause()
    return widget


async def await_agent(app, pilot):
    await asyncio.wait_for(app.client.called.wait(), 5)
    task = app._agent_task
    if task is not None:
        await asyncio.wait_for(task, 5)
    await pilot.pause()


@pytest.mark.asyncio
async def test_bare_at_lists_workspace_files_above_input(file_app):
    app = file_app
    async with app.run_test(size=(50, 20)) as pilot:
        show_chat(app)
        await pilot.press("@")
        await pilot.pause()
        popup = app.query_one(CompletionPopup)
        assert popup.is_visible
        assert popup.kind == "file"
        assert popup._values == ["alpha.py", "beta.py", "docs notes/"]
        assert popup.region.bottom <= app.query_one(ChatInput).region.y
        assert popup.region.y >= 0
        assert not app.client.prompts


@pytest.mark.asyncio
@pytest.mark.parametrize("selection_key", ["tab", "enter"])
async def test_file_completion_preserves_prompt_and_requires_second_enter_to_send(file_app, setup, selection_key):
    app = file_app
    authorization = setup.controller.authorization.records
    before = len(authorization)
    async with app.run_test(size=(90, 30)) as pilot:
        show_chat(app)
        widget = await type_input(app, pilot, "请看看 @a")
        assert app.query_one(CompletionPopup).is_visible
        await pilot.press(selection_key)
        await pilot.pause()
        assert widget.text == "请看看 @alpha.py "
        assert not app.query_one(CompletionPopup).is_visible
        assert not app.client.prompts
        assert len(authorization) == before

        await pilot.press("enter")
        await await_agent(app, pilot)
        assert len(app.client.prompts) == 1
        prompt = app.client.prompts[0][-1].content
        assert prompt.startswith("请看看 [File: alpha.py]")
        assert "ATTACHMENT_ONLY_ALPHA" in prompt
        assert widget.text == ""
        # The real _dispatch_command -> _send_message(direct_input=...) path
        # records the user's typed intent, never the attached file as consent.
        assert len(authorization) == before + 1
        assert authorization[-1]["text"] == "请看看 @alpha.py"
        assert "ATTACHMENT_ONLY" not in str(authorization)

        await app._send_message("Skill正文 @alpha.py", direct_input=None)
        await pilot.pause()
        assert app.client.prompts[-1][-1].content == "Skill正文 @alpha.py"
        assert len(authorization) == before + 1


@pytest.mark.asyncio
async def test_clicking_second_file_row_selects_that_file_without_sending(file_app):
    app = file_app
    async with app.run_test(size=(80, 24)) as pilot:
        show_chat(app)
        widget = await type_input(app, pilot, "查看 @")
        popup = app.query_one(CompletionPopup)
        assert popup._values[:2] == ["alpha.py", "beta.py"]
        await pilot.click(popup, offset=(3, 1))
        await pilot.pause()
        assert widget.text == "查看 @beta.py "
        assert widget.has_focus
        assert not popup.is_visible
        assert not app.client.prompts


@pytest.mark.asyncio
async def test_directory_with_spaces_drills_down_then_attaches_selected_file(file_app):
    app = file_app
    async with app.run_test(size=(80, 24)) as pilot:
        show_chat(app)
        widget = await type_input(app, pilot, "请读 @d")
        await pilot.press("tab")
        await pilot.pause()
        popup = app.query_one(CompletionPopup)
        assert widget.text.startswith('请读 @"docs notes/')
        assert popup.is_visible
        assert popup._values == ["docs notes/a.md"]
        assert not app.client.prompts
        await pilot.press("enter")
        await pilot.pause()
        assert widget.text == '请读 @"docs notes/a.md" '
        assert not popup.is_visible
        assert not app.client.prompts
        await pilot.press("enter")
        await await_agent(app, pilot)
        assert "[File: docs notes/a.md]" in app.client.prompts[0][-1].content
        assert "ATTACHMENT_ONLY_QUOTED" in app.client.prompts[0][-1].content


@pytest.mark.asyncio
async def test_completion_in_middle_replaces_reference_and_preserves_surrounding_text(file_app):
    app = file_app
    async with app.run_test(size=(80, 24)) as pilot:
        show_chat(app)
        widget = await type_input(app, pilot, "请比较 @alZZ 和后面的说明", cursor=len("请比较 @al"))
        assert app.query_one(CompletionPopup).get_selected() == "alpha.py"
        await pilot.press("tab")
        await pilot.pause()
        assert widget.text == "请比较 @alpha.py 和后面的说明"
        assert widget.cursor_location == (0, len("请比较 @alpha.py "))
        assert not app.query_one(CompletionPopup).is_visible
        assert not app.client.prompts


@pytest.mark.asyncio
async def test_no_matches_and_cursor_leaving_reference_close_popup(file_app):
    app = file_app
    async with app.run_test(size=(80, 24)) as pilot:
        show_chat(app)
        await type_input(app, pilot, "@missing")
        popup = app.query_one(CompletionPopup)
        assert not popup.is_visible
        widget = await type_input(app, pilot, "@a")
        assert popup.is_visible
        widget.move_cursor((0, 0))
        await pilot.pause()
        assert not popup.is_visible
        widget.move_cursor((0, len(widget.text)))
        await pilot.pause()
        assert popup.is_visible
        await pilot.press("space")
        await pilot.pause()
        assert not popup.is_visible
        assert not app.client.prompts


@pytest.mark.asyncio
@pytest.mark.parametrize("selection_key", ["tab", "enter"])
async def test_slash_command_tab_and_enter_keep_original_behavior(file_app, selection_key):
    app = file_app
    handler = AsyncMock()
    app.command_registry = CommandRegistry()
    app.command_registry.register_sync(Command("cmd", "Test command", CommandType.LOCAL, handler))
    async with app.run_test(size=(80, 24)) as pilot:
        show_chat(app)
        widget = await type_input(app, pilot, "/c")
        popup = app.query_one(CompletionPopup)
        assert popup.is_visible
        assert popup.kind == "command"
        await pilot.press(selection_key)
        await pilot.pause()
        if selection_key == "tab":
            assert widget.text == "/cmd "
            handler.assert_not_awaited()
            await pilot.press("enter")
            await pilot.pause()
        handler.assert_awaited_once()
        assert widget.text == ""
        assert not app.client.prompts
