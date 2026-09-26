from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static
from rich.text import Text

from nanocursor.agent import PermissionResponse


_PERM_OPTIONS = [
    ("Yes", PermissionResponse.ALLOW),
    ("Yes, and don't ask again for this pattern", PermissionResponse.ALLOW_ALWAYS),
    ("No", PermissionResponse.DENY),
]


class InlinePermissionWidget(Vertical, can_focus=True):
    """渲染在聊天区域内部的内联权限确认提示。

    与 Go 版 TUI 的权限对话框一致：工具名 + 描述 + 带编号的
    选项，支持方向键导航 + 回车确认。
    """

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "deny", "Deny", priority=True),
    ]

    class Responded(Message):


        def __init__(self, response: PermissionResponse) -> None:
            super().__init__()
            self.response = response

    def __init__(self, tool_name: str, description: str, *, reason: str = "", cwd: str = "",
                 allow_always: bool = True, allow_edits: bool = False, **kwargs) -> None:
        super().__init__(id="perm-inline", **kwargs)
        self._tool_name = tool_name
        self._description = description
        self._reason = reason
        self._cwd = cwd
        self._options = list(_PERM_OPTIONS) if allow_always else [
            ("仅允许这次", PermissionResponse.ALLOW), ("拒绝", PermissionResponse.DENY)]
        if allow_edits and tool_name in {"WriteFile", "EditFile"}:
            self._options.insert(1, ("允许本次，并开启自动编辑（本次运行）", PermissionResponse.ALLOW_EDITS))
        self._cursor = 0

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="perm-content")


    def on_mount(self) -> None:
        self.focus()

    def _build_content(self) -> Text:
        # Text treats shell/model strings as literal text, never Rich markup.
        text = Text(f"\n  {self._tool_name} command\n", style="yellow")
        if self._cwd:
            text.append(f"  cwd: {self._cwd}\n", style="dim")
        text.append(f"\n    {self._description}\n\n", style="default")
        text.append(f"  {self._reason or 'This command requires approval'}\n\n", style="default")
        for i, (label, _resp) in enumerate(self._options):
            text.append(f"  {'❯' if i == self._cursor else ' '} {i + 1}. {label}\n",
                        style="bold cyan" if i == self._cursor else "dim")
        return text


    def _refresh(self) -> None:
        content = self.query_one("#perm-content", Static)
        content.update(self._build_content())

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._options) - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        _, response = self._options[self._cursor]
        self.post_message(self.Responded(response))


    def action_deny(self) -> None:
        self.post_message(self.Responded(PermissionResponse.DENY))
