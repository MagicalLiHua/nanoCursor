"""
Slash command router for nanoCursor CLI.

Handles /help, /files, /config, /metrics, /clear, /team, /memory, /task, etc.
"""

import os
import json
from pathlib import Path
from typing import Optional, Callable

from src.infra.config import WORKSPACE_DIR
from src.infra.llm_config import MODEL, API_KEY, BASE_URL
from src.infra.metrics import metrics as _metrics
from src.cli.renderer import (
    render_file_tree, render_metrics, render_config, render_help,
    render_success, render_error, render_info, render_warning,
    render_markdown, render_code, render_text,
)
from src.memory.manager import get_memory_manager
from src.team.team import get_team_manager


def get_workdir() -> Path:
    return Path(WORKSPACE_DIR).resolve()


# ── Command handlers ───────────────────────────────────────────────

def cmd_help(args: list[str]) -> None:
    render_help()


def cmd_clear(args: list[str]) -> str:
    """Signal to REPL to clear conversation. Returns special marker."""
    return "__CLEAR__"


def cmd_files(args: list[str]) -> None:
    wd = get_workdir()
    all_files = []
    for root, dirs, filenames in os.walk(wd):
        # Skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in filenames:
            if f.startswith("."):
                continue
            full = Path(root) / f
            rel = full.relative_to(wd)
            all_files.append(str(rel))

    render_file_tree(all_files)


def cmd_cat(args: list[str]) -> None:
    if not args:
        render_error("Usage: /cat <path>")
        return

    path = args[0]
    try:
        fp = (get_workdir() / path).resolve()
        if not str(fp).startswith(str(get_workdir())):
            render_error(f"Path escapes workspace: {path}")
            return

        if not fp.exists():
            render_error(f"File not found: {path}")
            return

        content = fp.read_text(encoding="utf-8")
        ext = fp.suffix.lstrip(".") or "text"
        render_code(content, language=ext)
    except Exception as e:
        render_error(str(e))


def cmd_config(args: list[str]) -> None:
    info = {
        "Workspace": str(get_workdir()),
        "Model": MODEL,
        "API Base": BASE_URL or "(default)",
        "API Key": "****" + (API_KEY[-4:] if API_KEY and len(API_KEY) > 4 else "(not set)") if API_KEY else "(not set)",
    }
    render_config(info)


def cmd_metrics(args: list[str]) -> None:
    summary = _metrics.dump_summary()
    # 添加记忆库统计
    try:
        mm = get_memory_manager()
        summary["memory_count"] = len(mm.get(limit=1000))
    except Exception:
        pass
    render_metrics(summary)


def cmd_workspace(args: list[str]) -> None:
    render_info(f"Workspace: {get_workdir()}")


def cmd_model(args: list[str]) -> None:
    if not args:
        render_info(f"Current model: {MODEL}")
        return

    # Hot-switch model via env var (temporary, for current session)
    new_model = args[0]
    os.environ["LLM_MODEL"] = new_model
    # Reload the module's MODEL reference
    import importlib
    import src.infra.llm_config as llm_cfg
    importlib.reload(llm_cfg)
    render_success(f"Model switched to: {new_model} (for this session)")


# ── Team commands ──────────────────────────────────────────────────

def cmd_team(args: list[str]) -> None:
    if not args:
        render_error("Usage: /team <spawn|list|send|inbox|shutdown> [args...]")
        return

    sub = args[0].lower()
    rest = args[1:]
    tm = get_team_manager()

    if sub == "spawn":
        if len(rest) < 3:
            render_error("Usage: /team spawn <name> <role> <prompt>")
            return
        name, role = rest[0], rest[1]
        prompt = " ".join(rest[2:])
        result = tm.spawn(name, role, prompt, autonomous=True)
        render_success(result)

    elif sub == "list":
        result = tm.list_all()
        render_text(result)

    elif sub == "send":
        if len(rest) < 2:
            render_error("Usage: /team send <name> <message>")
            return
        from src.team.team import BUS
        to = rest[0]
        content = " ".join(rest[1:])
        result = BUS.send("lead", to, content, "message")
        render_success(f"Sent to '{to}'")

    elif sub == "inbox":
        from src.team.team import BUS
        inbox = BUS.read_inbox("lead")
        if not inbox:
            render_text("(inbox empty)")
        else:
            for msg in inbox:
                render_text(f"[{msg.get('from', '?')}]: {msg.get('content', '')}")

    elif sub == "shutdown":
        if not rest:
            render_error("Usage: /team shutdown <name>")
            return
        from src.team.team import handle_shutdown_request
        result = handle_shutdown_request(rest[0])
        render_success(result)

    else:
        render_error(f"Unknown team subcommand: {sub}. Use: spawn, list, send, inbox, shutdown")


# ── Memory commands ────────────────────────────────────────────────

def cmd_memory(args: list[str]) -> None:
    if not args:
        render_error("Usage: /memory <save|search|list> [args...]")
        return

    sub = args[0].lower()
    rest = args[1:]
    mem = get_memory_manager()

    if sub == "save":
        if not rest:
            render_error("Usage: /memory save <content>")
            return
        content = " ".join(rest)
        result = mem.save(category="project", content=content, importance=5)
        render_success(f"Memory saved: {result['id'][:8]}")

    elif sub == "search":
        if not rest:
            render_error("Usage: /memory search <query>")
            return
        query = " ".join(rest)
        results = mem.search(query)
        if not results:
            render_text("No memories found.")
        else:
            formatted = mem.format_memories(results, max_items=20)
            render_text(formatted)

    elif sub == "list":
        results = mem.get(limit=50)
        if not results:
            render_text("No memories.")
        else:
            formatted = mem.format_memories(results, max_items=50)
            render_text(formatted)

    else:
        render_error(f"Unknown memory subcommand: {sub}. Use: save, search, list")


# ── Task commands ──────────────────────────────────────────────────

def cmd_task(args: list[str]) -> None:
    if not args:
        render_error("Usage: /task <create|list|update|graph> [args...]")
        return

    sub = args[0].lower()
    rest = args[1:]

    from src.agent.engine import get_task_manager as _get_tasks
    tm = _get_tasks()

    if sub == "create":
        if not rest:
            render_error("Usage: /task create <subject> [--blocks id1,id2]")
            return

        # Parse --blocks flag
        subject_parts = []
        blocked_by = []
        i = 0
        while i < len(rest):
            if rest[i] == "--blocks" and i + 1 < len(rest):
                blocked_by = [b.strip() for b in rest[i+1].split(",")]
                i += 2
            else:
                subject_parts.append(rest[i])
                i += 1

        subject = " ".join(subject_parts)
        task = tm.create(subject, "", blocked_by)
        render_success(f"Created task {task['id']}: {subject}")

    elif sub == "list":
        tasks = tm.list_all()
        if not tasks:
            render_text("No tasks.")
            return

        from rich.table import Table
        from rich.console import Console
        table = Table(title="📋 Tasks", box="ROUNDED")
        table.add_column("ID", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Subject", style="white")
        table.add_column("Blocked By", style="yellow")

        for t in tasks:
            table.add_row(
                t["id"],
                t.get("status", "?"),
                t.get("subject", "")[:80],
                ", ".join(t.get("blocked_by", [])) or "-"
            )

        Console().print(table)

    elif sub == "update":
        if len(rest) < 2:
            render_error("Usage: /task update <task_id> <status>")
            return
        task_id, status = rest[0], rest[1]
        tm.update_status(task_id, status)
        render_success(f"Updated task {task_id} -> {status}")

    elif sub == "graph":
        tasks = tm.list_all()
        if not tasks:
            render_text("No tasks.")
            return

        from rich.tree import Tree
        from rich.console import Console

        tree = Tree("📊 Task DAG")
        status_icons = {"pending": "○", "in_progress": "◉", "completed": "✓"}

        # Find roots (tasks not in any blocked_by)
        blocked_ids = set()
        for t in tasks:
            for b in t.get("blocked_by", []):
                blocked_ids.add(b)

        roots = [t for t in tasks if t["id"] not in blocked_ids]
        task_map = {t["id"]: t for t in tasks}

        def add_node(parent, task_id, visited=None):
            if visited is None:
                visited = set()
            if task_id in visited:
                return
            visited.add(task_id)

            t = task_map.get(task_id)
            if not t:
                parent.add(f"[red]? {task_id}[/red]")
                return

            icon = status_icons.get(t.get("status", ""), "?")
            label = f"{icon} [{t.get('status', '?')}] {t.get('subject', '?')} (id={task_id})"
            node = parent.add(label)

            # Find children (tasks blocked by this one)
            for ct in tasks:
                if task_id in ct.get("blocked_by", []):
                    add_node(node, ct["id"], visited)

        for root in roots:
            add_node(tree, root["id"])

        Console().print(tree)

    else:
        render_error(f"Unknown task subcommand: {sub}. Use: create, list, update, graph")


# ── Project command ─────────────────────────────────────────────────

def cmd_project(args: list[str]) -> None:
    """显示项目结构概览（索引+依赖图）"""
    from src.indexer.indexer import get_project_index
    from rich.tree import Tree
    from rich.console import Console
    from rich.panel import Panel

    idx = get_project_index()
    idx.build()

    s = idx.summary()

    # Build a Rich Tree
    root = Tree(f"[bold cyan]📦 {idx.workspace.name}[/bold cyan]")

    # Entry points
    ep_node = root.add("[bold green]入口点[/bold green]")
    for ep in s.get("entry_points", []):
        ep_node.add(f"🚀 {ep}")

    # Modules
    mod_node = root.add("[bold blue]模块[/bold blue]")
    modules = s.get("modules", {})
    shown = 0
    for mod_path, info in sorted(modules.items()):
        if shown >= 20:
            mod_node.add(f"[dim]... 及其他 {len(modules) - 20} 个模块[/dim]")
            break
        syms = [f"{sym['type']} [cyan]{sym['name']}[/cyan]" for sym in info.get("symbols", [])[:3]]
        label = mod_path
        if syms:
            label += f"  [dim]({', '.join(syms)})[/dim]"
        mod_node.add(label)
        shown += 1

    # Stats
    stats_node = root.add("[bold yellow]统计[/bold yellow]")
    stats_node.add(f"文件: {s['total_files']} 个 (source: {s['source_count']}, test: {s['test_count']}, config: {s['config_count']})")
    stats_node.add(f"代码量: {s['total_loc']:,} 字节")

    # Recent changes
    if s.get("recently_modified"):
        rc_node = root.add("[bold magenta]最近修改[/bold magenta]")
        for path, _ in s["recently_modified"][:5]:
            rc_node.add(path)

    Console().print(root)


# ── Command router ─────────────────────────────────────────────────

COMMAND_MAP: dict[str, Callable] = {
    "help": cmd_help,
    "h": cmd_help,
    "clear": cmd_clear,
    "files": cmd_files,
    "ls": cmd_files,
    "cat": cmd_cat,
    "config": cmd_config,
    "metrics": cmd_metrics,
    "workspace": cmd_workspace,
    "pwd": cmd_workspace,
    "model": cmd_model,
    "project": cmd_project,
    "team": cmd_team,
    "memory": cmd_memory,
    "task": cmd_task,
}


class CommandRouter:
    """Routes slash commands to their handlers."""

    def route(self, text: str) -> Optional[str]:
        """
        Parse and execute a slash command.
        Returns "__CLEAR__" if conversation should be cleared, None otherwise.
        """
        if not text.startswith("/"):
            return None

        # Parse: /command arg1 arg2 ...
        parts = text[1:].strip().split()
        if not parts:
            return None

        cmd_name = parts[0].lower()
        cmd_args = parts[1:]

        handler = COMMAND_MAP.get(cmd_name)
        if handler is None:
            render_error(f"Unknown command: /{cmd_name}. Type /help for available commands.")
            return None

        result = handler(cmd_args)
        return result if isinstance(result, str) else None


def handle_command(text: str) -> Optional[str]:
    """Convenience function to route a command string."""
    router = CommandRouter()
    return router.route(text)


def is_command(text: str) -> bool:
    """Check if text is a slash command."""
    return text.strip().startswith("/")
