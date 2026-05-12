"""
NanoREPL - Interactive REPL loop for nanoCursor CLI.

Inspired by Claude Code's interactive design:
- / triggers a command palette dropdown with descriptions
- Tab completion with hierarchical command support
- Clean visual separation between turns
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from src.cli.renderer import (
    render_markdown, render_tool_call, render_status,
    render_success, render_error, render_info, render_warning, render_text,
)
from src.cli.commands import is_command, handle_command

console = Console(highlight=False)

# Try importing prompt_toolkit, fall back to basic input()
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import Completer, Completion, NestedCompleter
    from prompt_toolkit.styles import Style
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.formatted_text import HTML
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


def _get_history_file() -> Path:
    """Get path to CLI history file."""
    from src.infra.config import WORKSPACE_DIR
    history_dir = Path(WORKSPACE_DIR) / ".nanocursor"
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir / "cli_history.txt"


# ── Command palette data ────────────────────────────────────────────

# Each command: (name, args_hint, description)
COMMAND_PALETTE = [
    # Chat
    ("help", "", "显示所有命令"),
    ("clear", "", "开始新对话"),
    # Files
    ("files", "", "列出工作区文件"),
    ("cat", "<path>", "查看文件内容（语法高亮）"),
    # System
    ("config", "", "显示 LLM 配置"),
    ("metrics", "", "显示 LLM 调用统计"),
    ("workspace", "", "显示工作区路径"),
    ("model", "<name>", "切换 LLM 模型"),
    # Team
    ("team spawn", "<name> <role> <prompt>", "创建团队成员"),
    ("team list", "", "列出所有成员及状态"),
    ("team send", "<name> <msg>", "向成员发送消息"),
    ("team inbox", "", "查看收件箱"),
    ("team shutdown", "<name>", "请求成员关闭"),
    # Memory
    ("memory save", "<content>", "保存一条记忆"),
    ("memory search", "<query>", "搜索记忆"),
    ("memory list", "", "列出所有记忆"),
    # Tasks
    ("task create", "<subject>", "创建任务"),
    ("task list", "", "列出所有任务"),
    ("task update", "<id> <status>", "更新任务状态"),
    ("task graph", "", "显示任务依赖图"),
]


class NanoCompleter(Completer):
    """Custom completer that shows command palette with descriptions."""

    def __init__(self):
        self.commands = COMMAND_PALETTE

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # Only activate for / commands
        if not text.startswith("/"):
            return

        # Get the word being typed (everything after /)
        word = text.lstrip("/")

        for cmd, args, desc in self.commands:
            if cmd.startswith(word):
                display_text = f"/{cmd} {args}".strip()

                # Build formatted display
                fragments = [("ansicyan", f"/{cmd}")]
                if args:
                    fragments.append(("", "  "))
                    fragments.append(("ansibrightblack", args))

                from prompt_toolkit.formatted_text import to_formatted_text

                yield Completion(
                    text=display_text,
                    start_position=-len(text),  # replace the entire /... typed so far
                    display=to_formatted_text(fragments),
                    display_meta=to_formatted_text([("ansiyellow", desc)]),
                )


def _format_tokens(n: int) -> str:
    """Format token count like Claude Code: 2100 → '2.1k'."""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


# ── NanoREPL class ─────────────────────────────────────────────────

class NanoREPL:
    """Interactive REPL for nanoCursor, Claude Code style."""

    def __init__(self):
        self.messages: list[dict] = []
        self.session_id: str = ""
        self.running = True
        self._session: Optional["PromptSession"] = None
        # Cumulative session token totals
        self.total_inp: int = 0
        self.total_out: int = 0

    # ── Public API ──────────────────────────────────────────────────

    async def run(self) -> None:
        """Start the REPL loop."""
        self._print_banner()

        while self.running:
            try:
                # Separator line above input (Claude Code style)
                console.print(Rule(style="dim"))

                # Build rprompt showing cumulative session tokens
                inp_str = _format_tokens(self.total_inp)
                out_str = _format_tokens(self.total_out)
                rprompt = f"↑ {inp_str} ↓ {out_str} tokens" if self.total_inp > 0 else ""

                user_input = await self._get_input(rprompt)

                # Separator line below input
                console.print(Rule(style="dim"))

                if user_input is None:
                    self.running = False
                    break

                user_input = user_input.strip()
                if not user_input:
                    continue

                # Check for slash commands
                if is_command(user_input):
                    result = handle_command(user_input)
                    if result == "__CLEAR__":
                        self.messages = []
                        self.session_id = ""
                        self.total_inp = 0
                        self.total_out = 0
                        render_success("已清空对话。")
                    continue

                # Route to agent
                console.print()
                await self._run_agent(user_input)

            except KeyboardInterrupt:
                console.print("\n[dim]按 Ctrl+D 退出，或继续输入对话。[/dim]")
            except EOFError:
                self.running = False
                break

        console.print("\n[dim]再见！[/dim]")

    # ── Internal ────────────────────────────────────────────────────

    def _print_banner(self) -> None:
        """Print welcome banner."""
        from src.infra.llm_config import MODEL

        banner = f"""
[bold cyan]  nanoCursor[/bold cyan] [dim]v0.1[/dim]  [dim]{MODEL}[/dim]
[dim]  输入 /help 查看命令，直接输入自然语言开始对话[/dim]
"""
        console.print(banner)

    async def _get_input(self, rprompt: str = "") -> Optional[str]:
        """Get user input, with prompt_toolkit if available."""
        if PROMPT_TOOLKIT_AVAILABLE:
            return await self._prompt_toolkit_input(rprompt)
        else:
            return self._basic_input()

    async def _prompt_toolkit_input(self, rprompt: str = "") -> Optional[str]:
        """Get input via prompt_toolkit with command palette."""
        if self._session is None:
            completer = NanoCompleter()

            style = Style.from_dict({
                "prompt": "#e0e0e0 bold",
                "separator": "#555555",
                "rprompt": "#888888",
                "completion-menu": "bg:#2a2a2a #cccccc",
                "completion-menu.completion": "bg:#2a2a2a #cccccc",
                "completion-menu.completion.current": "bg:#444444 #ffffff bold",
                "completion-menu.meta": "bg:#2a2a2a #888888",
                "completion-menu.meta.current": "bg:#444444 #aaaaaa bold",
                "bottom-toolbar": "bg:#2a2a2a #666666",
            })

            bindings = KeyBindings()

            @bindings.add("escape", "enter")
            def _(event):
                """Alt+Enter to insert newline."""
                event.current_buffer.insert_text("\n")

            try:
                history_file = _get_history_file()
                self._session = PromptSession(
                    history=FileHistory(str(history_file)),
                    auto_suggest=AutoSuggestFromHistory(),
                    completer=completer,
                    style=style,
                    key_bindings=bindings,
                    multiline=False,
                    complete_while_typing=True,
                    complete_in_thread=True,
                )
            except Exception:
                self._session = PromptSession(
                    completer=completer,
                    style=style,
                    key_bindings=bindings,
                    multiline=False,
                    complete_while_typing=True,
                )

        try:
            text = await self._session.prompt_async(
                [
                    ("class:prompt", "nanoCursor"),
                    ("class:separator", " › "),
                ],
                rprompt=[("class:rprompt", f"  {rprompt}  ")] if rprompt else None,
            )
            return text
        except (EOFError, KeyboardInterrupt):
            return None

    def _basic_input(self) -> Optional[str]:
        """Fallback basic input()."""
        try:
            return input("nanoCursor › ")
        except (EOFError, KeyboardInterrupt):
            return None

    async def _run_agent(self, user_input: str) -> None:
        """Run the agent with the user's input."""
        from src.agent.engine import agent_loop, ALL_TOOLS, SystemPromptBuilder
        from src.infra.metrics import metrics as _metrics
        from rich.live import Live
        from rich.text import Text
        import time as _time

        # Add user message
        self.messages.append({"role": "user", "content": user_input})

        # Build system prompt
        builder = SystemPromptBuilder(tools=ALL_TOOLS)
        system = builder.build()

        token_state = {"inp": 0, "out": 0}

        def on_tool_call(tool_name: str, tool_input: dict, output: str):
            render_tool_call(tool_name, tool_input, output)

        def on_token_update(input_tokens: int, output_tokens: int):
            token_state["inp"] += input_tokens
            token_state["out"] += output_tokens

        t0 = _time.time()

        live = Live(Text("  Thinking… 1s", style="bold #999999"), console=console,
                    refresh_per_second=4, transient=False)
        live.start()

        # Background task to tick the timer every second
        async def tick_timer():
            while True:
                await asyncio.sleep(1)
                elapsed = int(_time.time() - t0) or 1
                live.update(Text(f"  Thinking… {elapsed}s", style="bold #999999"))

        tick_task = asyncio.create_task(tick_timer())

        result = ""
        try:
            result = await agent_loop(
                messages=self.messages,
                system=system,
                tools=ALL_TOOLS,
                max_turns=100,
                on_tool_call=on_tool_call,
                on_llm_response=on_token_update,
            )
        except Exception as e:
            result = f"Error: {e}"
        finally:
            tick_task.cancel()
            try:
                await tick_task
            except asyncio.CancelledError:
                pass
            elapsed = int(_time.time() - t0) or 1
            live.update(Text(f"  Thought for {elapsed}s", style="bold #777777"))
            live.stop()

        # Add assistant response to message history
        if result and not result.startswith("Error:"):
            self.messages.append({
                "role": "assistant",
                "content": [{"type": "text", "text": result}]
            })

        # Accumulate session token totals
        self.total_inp += token_state["inp"]
        self.total_out += token_state["out"]

        # Render the final result
        console.print()
        if result.startswith("Error:"):
            render_error(result)
        elif result == "(max turns reached)":
            render_warning("达到最大轮数，任务可能需要更多步骤。")
        else:
            render_markdown(result)


# ── Entry point ────────────────────────────────────────────────────

def run_repl() -> None:
    """Run the nanoCursor REPL."""
    repl = NanoREPL()
    try:
        asyncio.run(repl.run())
    except KeyboardInterrupt:
        console.print("\n[dim]已中断。再见！[/dim]")
    except Exception as e:
        render_error(f"致命错误: {e}")
        sys.exit(1)
