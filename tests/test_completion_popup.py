from __future__ import annotations

import pytest
from textual.app import App

from nanocursor.commands.completion import CompletionPopup


class CompletionApp(App):
    BINDINGS = [("down", "next_item", "Next"), ("up", "previous_item", "Previous"),
                ("enter", "accept_item", "Accept")]

    def __init__(self):
        super().__init__()
        self.selections = []

    def compose(self):
        yield CompletionPopup()

    def action_next_item(self):
        self.query_one(CompletionPopup).move_down()

    def action_previous_item(self):
        self.query_one(CompletionPopup).move_up()

    def action_accept_item(self):
        self.query_one(CompletionPopup).accept_selected()

    def on_completion_popup_selected(self, event):
        self.selections.append((event.value, event.kind))


@pytest.mark.asyncio
async def test_mouse_selects_clicked_row_and_preserves_file_kind():
    app = CompletionApp()
    async with app.run_test(size=(50, 20)) as pilot:
        popup = app.query_one(CompletionPopup)
        popup.show(["@alpha.py", "@beta.py", "@gamma.py"], kind="file")
        await pilot.pause()
        await pilot.click(popup, offset=(2, 1))
        await pilot.pause()
        assert app.selections == [("@beta.py", "file")]
        assert not popup.is_visible


@pytest.mark.asyncio
async def test_keyboard_scrolls_current_item_into_six_row_window():
    app = CompletionApp()
    async with app.run_test(size=(50, 20)) as pilot:
        popup = app.query_one(CompletionPopup)
        popup.show([f"@file-{i}.py" for i in range(10)], kind="file")
        await pilot.pause()
        await pilot.press(*(["down"] * 8))
        await pilot.pause()
        assert popup.get_selected() == "@file-8.py"
        assert popup._window_start == 3
        assert "@file-8.py" in popup.render().plain
        assert "@file-0.py" not in popup.render().plain
        assert len(popup.render().plain.splitlines()) == 6
        assert popup.region.height == 6

        await pilot.press(*(["up"] * 8))
        await pilot.pause()
        assert popup.get_selected() == "@file-0.py"
        assert popup._window_start == 0
        await pilot.press(*(["down"] * 8), "enter")
        await pilot.pause()
        assert app.selections == [("@file-8.py", "file")]


@pytest.mark.asyncio
async def test_mouse_uses_scrolled_window_index_and_literal_display():
    app = CompletionApp()
    async with app.run_test(size=(50, 20)) as pilot:
        popup = app.query_one(CompletionPopup)
        names = [f"@file-{i}.py" for i in range(10)]
        names[4] = "@[/missing]/[bold]文件.txt"
        popup.show(names, kind="file")
        await pilot.press(*(["down"] * 8))
        await pilot.pause()
        assert names[4] in popup.render().plain
        await pilot.click(popup, offset=(2, 1))
        await pilot.pause()
        assert app.selections == [(names[4], "file")]


@pytest.mark.asyncio
async def test_command_defaults_and_show_pairs_remain_compatible():
    app = CompletionApp()
    async with app.run_test(size=(50, 20)) as pilot:
        popup = app.query_one(CompletionPopup)
        popup.show_pairs([("打开状态", "/status"), ("帮助", "/help")])
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.selections == [("/status", "command")]
        assert CompletionPopup.Selected("/help").kind == "command"
        popup.show(["/help"])
        assert popup.kind == "command"
        popup.show([])
        assert not popup.is_visible
        assert popup.get_selected() is None
