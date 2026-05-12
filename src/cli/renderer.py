"""
Rich console renderer for nanoCursor CLI.

Handles: markdown, code blocks, tool call output, streaming text,
status panels, metrics tables, and file tree display.
"""

import re
import sys
import textwrap
from typing import Optional

# Ensure UTF-8 encoding on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich.layout import Layout
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

console = Console(highlight=False, force_terminal=True)


# ── Markdown rendering ────────────────────────────────────────────

def render_markdown(text: str) -> None:
    """Render markdown text to the console."""
    md = Markdown(text, code_theme="monokai")
    console.print(md)


def render_text(text: str) -> None:
    """Render plain text, preserving line breaks."""
    console.print(text)


# ── Code blocks ────────────────────────────────────────────────────

def render_code(code: str, language: str = "python") -> None:
    """Render a syntax-highlighted code block."""
    syntax = Syntax(code, language, theme="monokai", line_numbers=True, word_wrap=True)
    console.print(syntax)


def render_diff(diff_text: str) -> None:
    """Render a diff with coloring."""
    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
    console.print(syntax)


# ── Tool call display ──────────────────────────────────────────────

def render_tool_call(tool_name: str, tool_input: dict, output: str, duration_ms: float = 0) -> None:
    """Render a collapsed one-line tool call (Claude Code style)."""

    # Compact input: show only first key-value, truncate values
    input_parts = []
    for k, v in list(tool_input.items())[:2]:
        val_str = str(v).replace("\n", " ")[:60]
        input_parts.append(f"{k}={val_str}")
    if len(tool_input) > 2:
        input_parts.append(f"+{len(tool_input) - 2}")

    input_summary = ", ".join(input_parts)

    # Compact output summary
    out_first_line = output.strip().split("\n")[0][:100]
    out_len = len(output)

    if out_len <= 200:
        out_summary = out_first_line
    else:
        out_summary = f"{out_first_line}... ({_fmt_size(out_len)})"

    # Build the collapsed line
    text = Text()
    text.append("  ", style="")
    text.append(tool_name, style="bold #888888")
    if input_summary:
        text.append(f"({input_summary})", style="#666666")
    text.append(" → ", style="#555555")
    text.append(out_summary, style="italic #777777")

    console.print(text)


def render_tool_call_expanded(tool_name: str, tool_input: dict, output: str) -> None:
    """Show full tool call details (expanded view)."""
    # Input
    input_lines = []
    for k, v in tool_input.items():
        val_str = str(v)
        if len(val_str) > 200:
            val_str = val_str[:197] + "..."
        input_lines.append(f"  {k}: {val_str}")

    input_text = "\n".join(input_lines) if input_lines else "  (empty)"

    # Output (first 3000 chars)
    output_display = output[:3000]
    if len(output) > 3000:
        output_display += f"\n... ({len(output) - 3000} more chars)"

    content = f"[bold]Input:[/bold]\n{input_text}\n\n[bold]Output:[/bold]\n{output_display}"

    panel = Panel(content, title=f"Tool: {tool_name}", border_style="blue", box=box.ROUNDED)
    console.print(panel)


def _fmt_size(n: int) -> str:
    """Format size: 1234 → '1.2k'."""
    if n >= 1000:
        return f"{n / 1000:.1f}k chars"
    return f"{n} chars"


# ── Status & progress ──────────────────────────────────────────────

def render_status(message: str, style: str = "bold blue") -> None:
    """Print a single status line."""
    console.print(f"[{style}]{message}[/{style}]")


def render_success(message: str) -> None:
    """Print a success line."""
    console.print(f"[bold green]✓[/bold green] {message}")


def render_error(message: str) -> None:
    """Print an error line."""
    console.print(f"[bold red]✗ {message}[/bold red]")


def render_warning(message: str) -> None:
    """Print a warning line."""
    console.print(f"[bold yellow]⚠ {message}[/bold yellow]")


def render_info(message: str) -> None:
    """Print an info line."""
    console.print(f"[bold blue]ℹ[/bold blue] {message}")


# ── Metrics display ────────────────────────────────────────────────

def _fmt_tokens(n: int) -> str:
    """Format token count: 2100 → '2.1k'."""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def render_metrics(metrics_data: dict) -> None:
    """Render metrics summary as a table."""
    llm = metrics_data.get("llm", {})

    table = Table(title="Token 用量", box=box.ROUNDED, border_style="blue")
    table.add_column("方向", style="cyan", no_wrap=True)
    table.add_column("Token 数", style="green")

    inp = llm.get("total_input_tokens", 0)
    out = llm.get("total_output_tokens", 0)

    table.add_row("↑ 输入 (prompt)", _fmt_tokens(inp))
    table.add_row("↓ 输出 (completion)", _fmt_tokens(out))
    table.add_row("∑ 合计", _fmt_tokens(inp + out))
    table.add_row("LLM 调用次数", str(llm.get("total_calls", 0)))
    table.add_row("平均延迟", f"{llm.get('avg_latency_ms', 0):.0f}ms")
    table.add_row("最大延迟", f"{llm.get('max_latency_ms', 0):.0f}ms")

    console.print(table)


# ── Config display ─────────────────────────────────────────────────

def render_config(config_info: dict) -> None:
    """Render configuration info."""
    table = Table(title="⚙️ Configuration", box=box.ROUNDED, border_style="blue")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    for key, value in config_info.items():
        table.add_row(key, str(value))

    console.print(table)


# ── File tree ──────────────────────────────────────────────────────

def render_file_tree(files: list[str], title: str = "📁 Workspace Files") -> None:
    """Render a file tree from a list of file paths."""
    if not files:
        console.print("[dim](no files)[/dim]")
        return

    tree = Tree(title)
    # Build a simple tree structure
    dir_nodes: dict[str, Tree] = {}

    for f in sorted(files):
        parts = f.replace("\\", "/").split("/")
        current = tree
        for i, part in enumerate(parts[:-1]):
            prefix = "/".join(parts[:i+1])
            if prefix not in dir_nodes:
                dir_nodes[prefix] = current.add(f"📁 {part}")
            current = dir_nodes[prefix]
        # Add file
        ext = parts[-1].split(".")[-1] if "." in parts[-1] else ""
        icon = "🐍" if ext == "py" else "📄"
        current.add(f"{icon} {parts[-1]}")

    console.print(tree)


# ── Streaming output ───────────────────────────────────────────────

class StreamingRenderer:
    """Handles streaming text display during agent execution."""

    def __init__(self):
        self._current_line = ""

    def on_token(self, token: str) -> None:
        """Called for each streaming token."""
        console.print(token, end="", highlight=False)

    def on_tool_start(self, tool_name: str) -> None:
        """Called when a tool call starts."""
        console.print(f"\n[dim]⏳ Calling {tool_name}...[/dim]")

    def on_tool_end(self, tool_name: str, _input: dict, output: str, duration_ms: float = 0) -> None:
        """Called when a tool call completes."""
        render_tool_call(tool_name, _input, output, duration_ms)

    def on_complete(self) -> None:
        """Called when agent response is complete."""
        console.print()

    def on_error(self, error: str) -> None:
        """Called on error."""
        render_error(error)


# ── Session list ───────────────────────────────────────────────────

def render_session_list(sessions: list[dict]) -> None:
    """Render a list of sessions."""
    if not sessions:
        console.print("[dim](no saved sessions)[/dim]")
        return

    table = Table(title="📜 Sessions", box=box.ROUNDED)
    table.add_column("ID", style="cyan")
    table.add_column("Prompt", style="white")
    table.add_column("Turns", style="green")
    table.add_column("Time", style="dim")

    for s in sessions:
        table.add_row(
            s.get("id", "?")[:16],
            s.get("prompt", "")[:60],
            str(s.get("turns", "?")),
            s.get("time", "?")
        )

    console.print(table)


# ── Message block rendering ────────────────────────────────────────

def render_message_block(block: dict) -> None:
    """Render a single message content block (text or tool_use)."""
    block_type = block.get("type", "")

    if block_type == "text":
        console.print(block.get("text", ""))
    elif block_type == "tool_use":
        name = block.get("name", "unknown")
        console.print(f"[dim]🔧 Calling {name}...[/dim]")
    elif block_type == "tool_result":
        content = block.get("content", "")
        if len(content) > 500:
            content = content[:497] + "..."
        console.print(f"[dim]{content}[/dim]")
    elif block_type == "thinking":
        console.print(f"[dim italic]{block.get('thinking', '')}[/dim italic]")


# ── Highlight help ─────────────────────────────────────────────────

def render_help() -> None:
    """Render compact slash-command help, Claude Code style."""

    sections = [
        ("对话", [
            ("/help", "显示所有命令"),
            ("/clear", "开始新对话"),
        ]),
        ("文件", [
            ("/files", "列出工作区文件"),
            ("/cat <path>", "查看文件（语法高亮）"),
        ]),
        ("系统", [
            ("/config", "LLM 配置"),
            ("/metrics", "调用统计"),
            ("/workspace", "工作区路径"),
            ("/model <name>", "切换模型"),
        ]),
        ("团队", [
            ("/team spawn <name> <role> <prompt>", "创建成员"),
            ("/team list", "成员列表"),
            ("/team send <name> <msg>", "发送消息"),
            ("/team inbox", "收件箱"),
            ("/team shutdown <name>", "关闭成员"),
        ]),
        ("记忆", [
            ("/memory save <content>", "保存记忆"),
            ("/memory search <query>", "搜索记忆"),
            ("/memory list", "记忆列表"),
        ]),
        ("任务", [
            ("/task create <subject>", "创建任务"),
            ("/task list", "任务列表"),
            ("/task update <id> <status>", "更新状态"),
            ("/task graph", "依赖图"),
        ]),
    ]

    t = Text()
    for section_name, commands in sections:
        t.append(f"\n[bold cyan]{section_name}[/bold cyan]\n")
        for cmd, desc in commands:
            t.append(f"  [bold white]{cmd}[/bold white]")
            t.append(f"  [dim]— {desc}[/dim]\n")

    console.print(Panel(t, title="nanoCursor 命令", border_style="cyan"))
