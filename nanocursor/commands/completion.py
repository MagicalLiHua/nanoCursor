from __future__ import annotations

from typing import Literal

from rich.text import Text
from textual.events import Click
from textual.message import Message as TMessage
from textual.widgets import Static


CompletionKind = Literal["command", "file"]


class CompletionPopup(Static):
    VISIBLE_ROWS = 6

    DEFAULT_CSS = """
    CompletionPopup {
        height: auto;
        max-height: 6;
        display: none;
        padding: 0 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    class Selected(TMessage):
        def __init__(self, value: str, kind: CompletionKind = "command") -> None:
            super().__init__()
            self.value = value
            self.kind = kind

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._displays: list[str] = []
        self._values: list[str] = []
        self._cursor: int = 0
        self._window_start: int = 0
        self.kind: CompletionKind = "command"

    def show_pairs(self, pairs: list[tuple[str, str]], *, kind: CompletionKind = "command") -> None:
        """以 (display_text, value) 对的形式显示候选项。"""
        if not pairs:
            self.hide()
            return
        self._displays = [d for d, _ in pairs]
        self._values = [v for _, v in pairs]
        self._cursor = 0
        self._window_start = 0
        self.kind = kind
        self._refresh_content()
        self.display = True

    def show(self, items: list[str], *, kind: CompletionKind = "command") -> None:
        self.show_pairs([(i, i) for i in items], kind=kind)

    def hide(self) -> None:
        self.display = False
        self._displays = []
        self._values = []
        self._cursor = 0
        self._window_start = 0

    @property
    def is_visible(self) -> bool:
        return bool(self.display)

    def move_up(self) -> None:
        if self._displays and self._cursor > 0:
            self._cursor -= 1
            self._refresh_content()

    def move_down(self) -> None:
        if self._displays and self._cursor < len(self._displays) - 1:
            self._cursor += 1
            self._refresh_content()

    def get_selected(self) -> str | None:
        if not self._values:
            return None
        return self._values[self._cursor]

    def accept_selected(self) -> None:
        selected = self.get_selected()
        if selected is not None:
            self.post_message(self.Selected(selected, self.kind))
            self.hide()

    def _refresh_content(self) -> None:
        if self._cursor < self._window_start:
            self._window_start = self._cursor
        elif self._cursor >= self._window_start + self.VISIBLE_ROWS:
            self._window_start = self._cursor - self.VISIBLE_ROWS + 1
        content = Text()
        visible = self._displays[self._window_start:self._window_start + self.VISIBLE_ROWS]
        for row, display in enumerate(visible):
            if row:
                content.append("\n")
            # Candidate text can be a filename; it must never become markup or
            # introduce extra visual rows that disagree with mouse selection.
            display = "".join(char if char.isprintable() else " " for char in display)
            i = self._window_start + row
            if i == self._cursor:
                content.append(f" {display} ", style="bold reverse")
            else:
                content.append(f"  {display}", style="dim")
        self.update(content)

    def on_click(self, event: Click) -> None:
        event.stop()
        offset = event.get_content_offset(self)
        if offset is None:
            return
        index = self._window_start + offset.y
        if 0 <= offset.y < self.VISIBLE_ROWS and index < len(self._values):
            self._cursor = index
            self.accept_selected()
