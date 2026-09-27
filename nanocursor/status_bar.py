"""Compact, width-aware terminal status with an on-demand detail view."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from nanocursor.status import StatusSnapshot, format_status_details


def compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}".removesuffix(".0") + "m"
    if value >= 1_000:
        return f"{value / 1_000:.1f}".removesuffix(".0") + "k"
    return str(max(0, value))


def _literal(value: str) -> str:
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())


def _fit(text: Text, width: int) -> Text:
    result = text.copy()
    result.truncate(max(0, width), overflow="ellipsis")
    return result


def status_lines(snapshot: StatusSnapshot, width: int) -> tuple[Text, Text]:
    """Fit terminal cells, including CJK, without losing the context indicator."""
    width = max(1, width)
    s = snapshot
    if s.context_window > 0:
        percent = max(0, int(s.context_used * 100 / s.context_window))
        percentage = "<1%" if s.context_used > 0 and percent == 0 else f"{percent}%"
        numbers = f"~{compact_number(s.context_used)}/{compact_number(s.context_window)} · {percentage}"
        context_choices = [f"上下文 {numbers}", f"上下文 {percentage}"]
        context_style = "red" if percent >= 95 else "yellow" if percent >= 80 else "#99a7b5"
    else:
        context_choices = ["上下文 —"]
        context_style = "dim"
    context = Text(context_choices[-1], style=context_style)
    for choice in context_choices:
        if Text(choice).cell_len <= width - 18:
            context = Text(choice, style=context_style)
            break
    left_width = max(1, width - context.cell_len - 2)
    model = Text(_literal(s.model) or "选择模型", style="bold")
    reasoning = Text(f" · 推理{_literal(s.reasoning)}", style="#99a7b5")
    left = model + reasoning if model.cell_len + reasoning.cell_len <= left_width else model
    left = _fit(left, left_width)
    first = left + Text(" " * max(1, width - left.cell_len - context.cell_len)) + context

    mode = {"default": "默认", "acceptEdits": "accept-edits", "plan": "计划",
            "bypassPermissions": "bypass!"}.get(s.permission_mode, s.permission_mode)
    mode_style = {"acceptEdits": "#7ac8b2", "plan": "yellow", "bypassPermissions": "bold red"}.get(s.permission_mode, "#99a7b5")
    approval = {"人工": "人工审批", "smart": "自动审批", "smart 暂停": "自动审批暂停",
                "smart 转人工": "转人工"}.get(s.approval, s.approval)
    approval_style = "bold yellow" if s.approval in {"审查中", "待确认"} else "#99a7b5"
    fields = [Text(_literal(mode), style=mode_style),
              Text(_literal(approval), style=approval_style)]
    mcp = f"MCP {s.mcp_connected}/{s.mcp_configured}" if s.mcp_configured else "MCP 0"
    if s.mcp_connecting:
        mcp += "…"
    fields.append(Text(mcp, style="yellow" if s.mcp_connecting or s.mcp_connected < s.mcp_configured else "#99a7b5"))
    if s.teammates:
        fields.append(Text(f"队友 {s.teammates}", style="#7ac8b2"))
    usage = f"Token ↓{compact_number(s.input_tokens)} ↑{compact_number(s.output_tokens)}"
    if s.usage_missing_requests:
        usage = (usage + "*" if s.input_tokens or s.output_tokens else "Token —")
    fields.extend([Text(f"工具 {s.tools_enabled}", style="#99a7b5"),
                   Text(usage, style="#99a7b5")])
    hint = Text("F2 详情", style="dim")
    available = max(1, width - hint.cell_len - 2)
    second = Text()
    for field in fields:
        candidate = second + Text("  ") + field if second else field
        if candidate.cell_len <= available:
            second = candidate
    if not second:
        second = _fit(fields[0], available)
    second += Text(" " * max(1, width - second.cell_len - hint.cell_len)) + hint
    return _fit(first, width), _fit(second, width)


class StatusBar(Vertical, can_focus=True):
    BINDINGS = [Binding("enter", "details", "状态详情")]

    class DetailsRequested(Message):
        pass

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.snapshot = StatusSnapshot()

    def compose(self) -> ComposeResult:
        yield Static("", id="model-label", markup=False)
        yield Static("", id="mode-label", markup=False)

    def set_snapshot(self, snapshot: StatusSnapshot) -> None:
        self.snapshot = snapshot
        self._refresh_lines()

    def _refresh_lines(self) -> None:
        if not self.is_mounted:
            return
        first, second = status_lines(self.snapshot, self.content_size.width or 80)
        self.query_one("#model-label", Static).update(first)
        self.query_one("#mode-label", Static).update(second)
        self.tooltip = Text("F2 或点击查看完整状态")

    def on_mount(self) -> None:
        self._refresh_lines()

    def on_resize(self) -> None:
        self._refresh_lines()

    def on_click(self, event) -> None:
        event.stop()
        self.action_details()

    def action_details(self) -> None:
        self.post_message(self.DetailsRequested())


class StatusDetailsScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "关闭", priority=True),
                Binding("f2", "close", "关闭", priority=True)]

    def __init__(self, snapshot: StatusSnapshot) -> None:
        super().__init__()
        self.snapshot = snapshot

    def compose(self) -> ComposeResult:
        with Vertical(id="status-dialog"):
            yield Static("运行状态", id="status-title")
            with VerticalScroll(id="status-detail-scroll"):
                yield Static(Text(format_status_details(self.snapshot, heading=False)), id="status-details")
            yield Button("关闭 · Esc", id="status-close")

    def set_snapshot(self, snapshot: StatusSnapshot) -> None:
        if snapshot != self.snapshot:
            self.snapshot = snapshot
            if self.is_mounted:
                self.query_one("#status-details", Static).update(Text(format_status_details(snapshot, heading=False)))

    def on_mount(self) -> None:
        self.query_one("#status-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "status-close":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss()
