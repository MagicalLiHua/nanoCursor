#!/usr/bin/env python3
"""
nanoCursor CLI - Interactive command-line coding assistant.

Usage:
    python cli.py              # Start interactive REPL
    python cli.py "prompt"     # One-shot: run a single prompt and exit
    python cli.py --help       # Show help

Based on the core agent engine (src/agent/engine.py) with all tools:
bash, read_file, write_file, edit_file, list_directory,
TodoWrite, TodoList, task_create, task_update, task_list,
spawn_teammate, send_message, broadcast, and more.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path (in case we're not running from project root)
_project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(override=False)


async def run_one_shot(prompt: str) -> None:
    """Run a single prompt and print the result."""
    from src.agent.engine import agent_loop, ALL_TOOLS, SystemPromptBuilder
    from src.infra.metrics import metrics as _metrics
    from src.cli.renderer import render_markdown, render_tool_call, render_error, render_warning

    builder = SystemPromptBuilder(tools=ALL_TOOLS)
    system = builder.build()
    messages = [{"role": "user", "content": prompt}]

    def on_tool(tool_name, tool_input, output):
        render_tool_call(tool_name, tool_input, output)

    result = await agent_loop(
        messages=messages,
        system=system,
        tools=ALL_TOOLS,
        max_turns=100,
        on_tool_call=on_tool,
    )

    if result.startswith("Error:"):
        render_error(result)
    elif result == "(max turns reached)":
        render_warning("Max turns reached.")
    else:
        render_markdown(result)

    # Show token stats
    summary = _metrics.dump_summary()
    llm = summary.get("llm", {})
    inp = f"{llm.get('total_input_tokens', 0)/1000:.1f}k" if llm.get('total_input_tokens', 0) >= 1000 else str(llm.get('total_input_tokens', 0))
    out = f"{llm.get('total_output_tokens', 0)/1000:.1f}k" if llm.get('total_output_tokens', 0) >= 1000 else str(llm.get('total_output_tokens', 0))
    print(f"\n── ↑ {inp} ↓ {out} tokens ──")


def main() -> None:
    """Entry point: dispatches to one-shot or interactive mode."""
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return

    # Check if there's a prompt argument (one-shot mode)
    prompt_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if prompt_args:
        prompt = " ".join(prompt_args)
        asyncio.run(run_one_shot(prompt))
    else:
        # Interactive REPL mode (handles its own asyncio.run)
        from src.cli.repl import run_repl
        run_repl()


if __name__ == "__main__":
    main()
