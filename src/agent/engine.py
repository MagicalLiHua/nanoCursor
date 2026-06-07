#!/usr/bin/env python3
"""
nanoCursor Core Engine - 统一 MVP 引擎

核心模块：
- agent_loop / agent_loop_stream: LLM 交互循环 + 工具调度
- SystemPromptBuilder: sections 管道式提示构建
- TodoManager / TaskManager: 任务管理
- BackgroundManager: 后台任务执行
- run_subagent / handle_spawn_agent: 子代理管理
"""

import os
import asyncio
import contextvars
import inspect
import json
import shlex
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Callable, Any
from dotenv import load_dotenv

load_dotenv(override=False)

from src.infra.llm_config import MODEL, API_KEY, BASE_URL, create_client, get_model_name
from src.infra.logger import logger
from src.infra.metrics import metrics as _metrics
from src.agent.state import WorkflowCancelledError
from src.tools.tool_result import is_tool_error_output

# ========== 配置 ==========
import src.infra.config as _config
from src.infra.config import LLM_MAX_TOKENS, LLM_TEMPERATURE

AGENT_BASH_TIMEOUT_SECONDS = 45

def get_workdir() -> Path:
    """Return the current workspace directory (always reads latest value from config)."""
    return Path(_config.WORKSPACE_DIR).resolve()


def reset_runtime_caches() -> None:
    """Clear workspace-scoped runtime singletons after the active workspace changes."""
    global _task_mgr, _todo_mgr
    _task_mgr = None
    _todo_mgr = None


_RUNTIME_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "nanocursor_runtime_context",
    default={},
)


@contextmanager
def bind_runtime_context(context: dict[str, Any] | None):
    """Temporarily bind run-scoped context for tool handlers."""
    token = _RUNTIME_CONTEXT.set(dict(context or {}))
    try:
        yield
    finally:
        _RUNTIME_CONTEXT.reset(token)


def get_runtime_context() -> dict[str, Any]:
    return dict(_RUNTIME_CONTEXT.get({}))

# Bootstrap initial directories
_initial_wd = get_workdir()
for d in [
    _initial_wd / ".team",
    _initial_wd / ".team" / "inbox",
    _initial_wd / ".tasks",
    _initial_wd / ".snapshots",
]:
    d.mkdir(parents=True, exist_ok=True)


# ========== 内置工具定义 ==========
BASE_TOOLS = [
    {"name": "bash", "description": "Run a shell command",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Edit file by line range (preferred) or text match. Use start_line/end_line for precise edits. Returns diff.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"},
         "start_line": {"type": "integer", "description": "1-indexed start line (for line-based edits)"},
         "end_line": {"type": "integer", "description": "1-indexed end line, inclusive (for line-based edits)"},
         "old_text": {"type": "string", "description": "Text to find and replace (legacy)"},
         "new_text": {"type": "string", "description": "Replacement text"},
     }, "required": ["path", "new_text"]}},
    {"name": "list_directory", "description": "List directory",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}},
    {"name": "run_tests", "description": "Run the project's test suite. Auto-detects framework (pytest/npm/go). Use after making code changes to verify correctness.",
     "input_schema": {"type": "object", "properties": {
         "test_path": {"type": "string", "description": "Optional: specific test file or directory to run"},
         "framework": {"type": "string", "description": "auto | pytest | npm | go. Default auto-detects."},
     }, "required": []}},
]

TASK_TOOLS = [
    {"name": "task_create", "description": "Create a task",
     "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}, "blocked_by": {"type": "array", "items": {"type": "string"}}}, "required": ["subject"]}},
    {"name": "task_update", "description": "Update task status",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "status": {"type": "string"}}, "required": ["task_id", "status"]}},
    {"name": "task_list", "description": "List tasks",
     "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}}},
]

MEMORY_TOOLS = [
    {"name": "add_memory", "description": "Store a persistent memory (user/feedback/project/reference)",
     "input_schema": {"type": "object", "properties": {
         "content": {"type": "string", "description": "Memory content"},
         "category": {"type": "string", "description": "user | feedback | project | reference"},
         "importance": {"type": "integer", "description": "0-10, >=7 auto-loads on new sessions"},
         "tags": {"type": "array", "items": {"type": "string"}, "description": "Search tags"},
     }, "required": ["content", "category"]}},
    {"name": "recall_memories", "description": "Search and retrieve persistent memories",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Search query"},
         "category": {"type": "string", "description": "Filter by category"},
         "limit": {"type": "integer", "description": "Max results"},
     }, "required": ["query"]}},
    {"name": "update_memory", "description": "Update an existing memory",
     "input_schema": {"type": "object", "properties": {
         "memory_id": {"type": "string", "description": "Memory ID to update"},
         "content": {"type": "string", "description": "New content"},
         "importance": {"type": "integer", "description": "New importance 0-10"},
     }, "required": ["memory_id"]}},
]

AGENT_RUNTIME_TOOLS = [
    {
        "name": "spawn_agent",
        "description": (
            "Create a run-scoped temporary Agent for a bounded task. Use this when the Lead "
            "needs a specialist with explicit role, scope, tools, and expected output. "
            "With run_now=true, the agent runs concurrently in the background (non-blocking). "
            "Use gather_agents later to collect results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Display name, e.g. Backend Reviewer"},
                "role": {"type": "string", "description": "Stable role id, e.g. backend_reviewer"},
                "goal": {"type": "string", "description": "Concrete task goal for this Agent"},
                "lifetime": {"type": "string", "description": "temporary only for runtime-created agents"},
                "reason": {"type": "string", "description": "Why this Agent is needed now"},
                "tools": {"type": "array", "items": {"type": "string"}},
                "capabilities": {"type": "array", "items": {"type": "string"}},
                "mcp_servers": {"type": "array", "items": {"type": "string"}},
                "blocked_capabilities": {"type": "array", "items": {"type": "string"}},
                "risk_level": {"type": "string"},
                "task_scope": {"type": "object"},
                "expected_output": {"type": "object"},
                "ttl_seconds": {"type": "integer"},
                "run_now": {
                    "type": "boolean",
                    "description": "Run this Agent concurrently in background (non-blocking). Use gather_agents to collect results.",
                },
            },
            "required": ["name", "role", "goal"],
        },
    },
    {
        "name": "gather_agents",
        "description": (
            "Wait for spawned agents to complete and collect their results. "
            "Blocks until all specified agents (or all agents if no IDs given) finish. "
            "Use after spawning agents with run_now=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Agent IDs to wait for. Omit to wait for all spawned agents.",
                },
            },
        },
    },
]

# Project tools
from src.tools.project_tools import PROJECT_TOOLS as _PROJECT_TOOLS
# Git tools
from src.tools.git_tools import GIT_TOOLS as _GIT_TOOLS

ALL_TOOLS = (
    BASE_TOOLS
    + TASK_TOOLS
    + MEMORY_TOOLS
    + AGENT_RUNTIME_TOOLS
    + _PROJECT_TOOLS
    + _GIT_TOOLS
)
# Alias for backwards compatibility
TOOLS = ALL_TOOLS


# ========== 工具处理函数 ==========
import importlib.util

from src.tools.path_safety import safe_path as _safe_path
from src.tools.bash import run_bash as _run_bash_impl
from src.tools.file_ops import (
    run_edit as _run_edit_impl,
    run_list_directory as _run_list_dir_impl,
    run_read as _run_read_impl,
    run_write as _run_write_impl,
)
from src.runtime.command_runner import run_command


def safe_path(p: str) -> Path:
    return _safe_path(p, get_workdir())


def run_bash(command: str) -> str:
    return _run_bash_impl(command, get_workdir(), timeout=AGENT_BASH_TIMEOUT_SECONDS)


def run_list_directory(path: str = ".") -> str:
    return _run_list_dir_impl(path, get_workdir())


def _parse_test_summary(output: str) -> tuple[int, int, int, str]:
    import re as _re

    passed = failed = errors = 0
    summary_line = ""
    for line in output.splitlines():
        passed_match = _re.search(r"(\d+)\s+passed", line)
        failed_match = _re.search(r"(\d+)\s+failed", line)
        error_match = _re.search(r"(\d+)\s+errors?", line)
        if not any((passed_match, failed_match, error_match)):
            continue
        passed = int(passed_match.group(1)) if passed_match else passed
        failed = int(failed_match.group(1)) if failed_match else failed
        errors = int(error_match.group(1)) if error_match else errors
        summary_line = line.strip()
    return passed, failed, errors, summary_line


def run_tests(test_path: str = "", framework: str = "auto") -> str:
    """Run the project's test suite and return results.

    Auto-detects the test framework (pytest, jest, go test, etc.)
    and runs relevant tests. If test_path is provided, runs only
    tests matching that path/pattern.

    Returns structured output with pass/fail counts and failure details.
    """
    wd = get_workdir()
    test_dir = wd / "tests"
    test_path_arg = test_path.strip() if test_path else ""
    env = None
    if (wd / "src").is_dir():
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        src_path = str((wd / "src").resolve())
        env["PYTHONPATH"] = src_path if not existing else f"{src_path}{os.pathsep}{existing}"

    if framework == "auto":
        # Auto-detect framework
        if (wd / "package.json").exists():
            pkg_json = wd / "package.json"
            try:
                import json as _json
                pkg = _json.loads(pkg_json.read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {})
                if "test" in scripts:
                    framework = "npm"
            except Exception:
                pass
        if framework == "auto" and (test_dir.exists() or (wd / "pytest.ini").exists() or (wd / "pyproject.toml").exists()):
            framework = "pytest"
        if framework == "auto" and (wd / "go.mod").exists():
            framework = "go"

    try:
        if framework == "pytest":
            cmd = ["python", "-m", "pytest"]
            if test_path_arg:
                cmd.append(test_path_arg)
            else:
                cmd.append("tests/")
            cmd.extend(["-v", "--tb=short"])
            if importlib.util.find_spec("pytest_timeout") is not None:
                cmd.append("--timeout=60")
            command_text = shlex.join(cmd)
            result = run_command(
                command_text,
                cwd=wd,
                timeout_seconds=120,
                max_stdout_chars=8000,
                max_stderr_chars=8000,
                permission_level="shell_safe",
                env=env,
            )
        elif framework == "npm":
            cmd = ["npm", "test"]
            if test_path_arg:
                cmd.extend(["--", test_path_arg])
            command_text = shlex.join(cmd)
            result = run_command(
                command_text,
                cwd=wd,
                timeout_seconds=120,
                max_stdout_chars=8000,
                max_stderr_chars=8000,
                permission_level="shell_safe",
            )
        elif framework == "go":
            cmd = ["go", "test", "./..."]
            if test_path_arg:
                cmd = ["go", "test", test_path_arg]
            command_text = shlex.join(cmd)
            result = run_command(
                command_text,
                cwd=wd,
                timeout_seconds=120,
                max_stdout_chars=8000,
                max_stderr_chars=8000,
                permission_level="shell_safe",
            )
        else:
            # Generic: just try pytest
            cmd = ["python", "-m", "pytest", "tests/", "-v", "--tb=short"]
            command_text = shlex.join(cmd)
            result = run_command(
                command_text,
                cwd=wd,
                timeout_seconds=120,
                max_stdout_chars=8000,
                max_stderr_chars=8000,
                permission_level="shell_safe",
                env=env,
            )
            framework = "pytest"

        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        returncode = int(result.get("exit_code") if result.get("exit_code") is not None else -1)
        if result.get("timed_out"):
            return "Tests timed out after 120s."

        output = stdout + stderr
        output = output[:8000]  # Limit output size

        # Parse pytest-style summary
        passed, failed, errors, summary_line = _parse_test_summary(output)

        if returncode == 0:
            if summary_line:
                return f"Command: {command_text}\nAll {passed} tests passed. ✓\n{summary_line}"
            return f"Command: {command_text}\nTest command completed successfully. ✓\n{output[-1200:]}"
        else:
            # Extract failure details
            failure_lines = []
            in_failure = False
            for line in output.splitlines():
                if "FAILURES" in line or "FAIL:" in line or "AssertionError" in line:
                    in_failure = True
                if in_failure:
                    failure_lines.append(line)
                if in_failure and ("short test summary" in line.lower() or "==" in line):
                    break
            failure_detail = "\n".join(failure_lines[-50:]) if failure_lines else output[-2000:]
            return (
                f"Tests: {passed} passed, {failed} failed, {errors} errors. ✗\n"
                f"Exit code: {returncode}\n"
                f"{summary_line}\n\n"
                f"Failure details:\n{failure_detail}"
            )
    except FileNotFoundError:
        return f"Test framework '{framework}' not found. Install it or specify a different framework."
    except Exception as e:
        return f"Error running tests: {e}"


def run_read(path: str, limit: int = None) -> str:
    return _run_read_impl(path, get_workdir(), limit)


def run_write(path: str, content: str) -> str:
    return _run_write_impl(path, content, get_workdir())


def run_edit(path: str, old_text: str = "", new_text: str = "", start_line: int = None, end_line: int = None) -> str:
    return _run_edit_impl(path, get_workdir(), old_text, new_text, start_line, end_line)


# ========== Todo & Task 管理器 ==========
from src.agent.managers import TodoManager, TaskManager


# ========== 子代理 ==========
async def run_subagent(
    prompt: str,
    system: str = None,
    agent_type: str = "Explore",
    tools: list | None = None,
) -> str:
    if system is None:
        system = f"You are a {agent_type} subagent at {get_workdir()}. Complete the task and summarize."
    if tools is None:
        tools = BASE_TOOLS

    client = create_client()
    messages = [{"role": "user", "content": prompt}]

    try:
        for turn in range(30):
            resp = await _retryable_llm_call(
                client, model=get_model_name(), system=system, messages=messages,
                tools=tools, max_tokens=LLM_MAX_TOKENS,
            )
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                break

            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    handler = TOOL_HANDLERS.get(block.name, lambda **kw: f"Unknown: {block.name}")
                    handled = handler(**block.input)
                    if inspect.isawaitable(handled):
                        handled = await handled
                    output = str(handled)[:50000]
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
            messages.append({"role": "user", "content": results})

        return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no summary)"
    finally:
        await client.close()


# ========== 并行工具执行 ==========
PARALLEL_TOOLS = {"read_file", "list_directory"}
WRITE_TOOLS = {"write_file", "edit_file"}


async def _execute_single_tool(
    tool_name: str,
    tool_input: dict,
    tool_id: str,
    on_tool_check: Callable | None,
    on_tool_call: Callable | None,
    on_cancel_check: Callable | None,
    session_id: str | None,
    file_lock=None,
) -> dict:
    """Execute a single tool call and return the tool_result dict."""
    await _raise_if_cancelled(on_cancel_check)

    decision = await _call_tool_check(on_tool_check, tool_name, tool_input)
    allowed, reason = _tool_policy_allows(decision)

    if not allowed:
        output = f"Error: Tool policy blocked: {reason}"
        _metrics.record_tool_failure(tool_name, output)
    else:
        try:
            handler = TOOL_HANDLERS.get(tool_name, lambda **kw: f"Unknown: {tool_name}")
            if not isinstance(tool_input, dict):
                output = f"Error: Invalid tool input format (expected dict, got {type(tool_input).__name__})"
                _metrics.record_tool_failure(tool_name, output)
            else:
                async def _run_handler():
                    return handler(**tool_input)

                if file_lock and tool_name in WRITE_TOOLS:
                    target_path = tool_input.get("path") or tool_input.get("file_path") or ""
                    if target_path:
                        handled = await file_lock.run_write(target_path, _run_handler())
                    else:
                        handled = await _run_handler()
                else:
                    handled = await _run_handler()

                if inspect.isawaitable(handled):
                    handled = await handled
                output = str(handled)[:50000]
                if is_tool_error_output(output):
                    _metrics.record_tool_failure(tool_name, output)
                    from src.agent.learner import get_learner
                    get_learner().on_tool_failure(tool_name, tool_input, output, session_id)
                else:
                    _metrics.record_tool_success(tool_name)
                    from src.agent.learner import get_learner
                    get_learner().on_tool_success(tool_name, tool_input, output)
        except Exception as tool_err:
            output = f"Error: {tool_err}"
            _metrics.record_tool_failure(tool_name, str(tool_err))
            from src.agent.learner import get_learner
            get_learner().on_tool_failure(tool_name, tool_input, output, session_id)

    if on_tool_call:
        on_tool_call(tool_name, tool_input, output)

    await _raise_if_cancelled(on_cancel_check)

    return {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": output,
    }


# ========== 工具处理函数字典 ==========
def _safe_handler(required: list[str], fn):
    """Wrap a handler to return clear error messages for missing params instead of KeyError."""
    def wrapper(**kw):
        missing = [k for k in required if k not in kw]
        if missing:
            return f"Error: Missing required parameter(s): {', '.join(missing)}"
        return fn(**kw)
    return wrapper

TOOL_HANDLERS: dict[str, Callable] = {
    "bash": _safe_handler(["command"], lambda command: run_bash(command)),
    "read_file": _safe_handler(["path"], lambda path, limit=None: run_read(path, limit)),
    "write_file": _safe_handler(["path", "content"], lambda path, content: run_write(path, content)),
    "edit_file": _safe_handler(["path", "new_text"], lambda path, old_text="", new_text="", start_line=None, end_line=None: run_edit(path, old_text, new_text, start_line, end_line)),
    "list_directory": lambda **kw: run_list_directory(kw.get("path", ".")),
    "run_tests": lambda test_path="", framework="auto", **kw: run_tests(test_path, framework),
}

# Compatibility-only Todo state for legacy modules. Todo tools are deliberately
# not exposed in ALL_TOOLS; the active model runtime uses the shared task board.
_todo_mgr = None
def get_todo_manager() -> TodoManager:
    global _todo_mgr
    if _todo_mgr is None:
        _todo_mgr = TodoManager(workdir=get_workdir())
    return _todo_mgr


# Task handlers
_task_mgr = None
def get_task_manager() -> TaskManager:
    global _task_mgr
    if _task_mgr is None:
        _task_mgr = TaskManager(workdir=get_workdir())
    return _task_mgr

def handle_task_create(subject: str, description: str = "", blocked_by: list = None) -> str:
    tm = get_task_manager()
    task = tm.create(subject, description, blocked_by)
    return f"Created task {task['id']}: {subject}"

def handle_task_update(task_id: str, status: str) -> str:
    tm = get_task_manager()
    tm.update_status(task_id, status)
    return f"Updated task {task_id} to {status}"

def handle_task_list(status: str = None) -> str:
    tm = get_task_manager()
    tasks = tm.list_all()
    if status:
        tasks = [t for t in tasks if t["status"] == status]
    if not tasks:
        return "No tasks"
    lines = []
    for t in tasks:
        lines.append(f"[{t['status']}] {t['subject']} (id={t['id']})")
    return "\n".join(lines)

TOOL_HANDLERS["task_create"] = lambda **kw: handle_task_create(kw.get("subject", ""), kw.get("description", ""), kw.get("blocked_by"))
TOOL_HANDLERS["task_update"] = lambda **kw: handle_task_update(kw.get("task_id", ""), kw.get("status", ""))
TOOL_HANDLERS["task_list"] = lambda **kw: handle_task_list(kw.get("status"))

# Memory tool handlers
def handle_add_memory(content: str, category: str, importance: int = 1, tags: list = None) -> str:
    from src.tools.memory_tools import add_memory
    context = get_runtime_context()
    return add_memory(
        content,
        category,
        importance,
        tags,
        workspace_dir=context.get("workspace_dir"),
        conversation_id=context.get("conversation_id"),
        run_id=context.get("thread_id"),
    )

def handle_recall_memories(query: str, category: str = None, limit: int = 10) -> str:
    from src.tools.memory_tools import recall_memories
    context = get_runtime_context()
    return recall_memories(
        query,
        category,
        limit,
        workspace_dir=context.get("workspace_dir"),
        conversation_id=context.get("conversation_id"),
        run_id=context.get("thread_id"),
    )

def handle_update_memory(memory_id: str, content: str = None, importance: int = None) -> str:
    from src.tools.memory_tools import update_memory
    context = get_runtime_context()
    return update_memory(memory_id, content, importance, workspace_dir=context.get("workspace_dir"))

TOOL_HANDLERS["add_memory"] = _safe_handler(["content", "category"], lambda content, category, importance=1, tags=None: handle_add_memory(content, category, importance, tags))
TOOL_HANDLERS["recall_memories"] = _safe_handler(["query"], lambda query, category=None, limit=10: handle_recall_memories(query, category, limit))
TOOL_HANDLERS["update_memory"] = _safe_handler(["memory_id"], lambda memory_id, content=None, importance=None: handle_update_memory(memory_id, content, importance))

# Project tool handlers
from src.tools.project_tools import search_codebase as _search_codebase, project_context as _project_context
TOOL_HANDLERS["search_codebase"] = _safe_handler(["query", "search_type"], lambda query, search_type="symbol": _search_codebase(query, search_type))
TOOL_HANDLERS["project_context"] = lambda **kw: _project_context()

# Git tool handlers
from src.tools.git_tools import (
    handle_git_status, handle_git_diff, handle_git_commit,
    handle_git_log, handle_git_reset, handle_git_file_history,
    set_git_workspace, ensure_git_repo,
)
TOOL_HANDLERS["git_status"] = lambda **kw: handle_git_status()
TOOL_HANDLERS["git_diff"] = lambda staged=False, **kw: handle_git_diff(staged=staged)
TOOL_HANDLERS["git_commit"] = _safe_handler(["message"], lambda message, **kw: handle_git_commit(message=message))
TOOL_HANDLERS["git_log"] = lambda count=10, **kw: handle_git_log(count=count)
TOOL_HANDLERS["git_reset"] = lambda mode="soft", ref="HEAD~1", confirmed=False, **kw: handle_git_reset(
    mode=mode,
    ref=ref,
    confirmed=confirmed,
)
TOOL_HANDLERS["git_file_history"] = _safe_handler(["filepath"], lambda filepath, count=5, **kw: handle_git_file_history(filepath=filepath, count=count))

def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _runtime_tool_names() -> set[str]:
    return {str(tool.get("name")) for tool in TOOLS if isinstance(tool, dict) and tool.get("name")}


def _tools_for_runtime_agent(agent: dict[str, Any]) -> list[dict[str, Any]]:
    """Return read-only tools allowed for a runtime child-agent proposal."""
    readonly = {"read_file", "list_directory", "search_codebase", "project_context", "git_status", "git_diff"}
    requested = set(_as_string_list(agent.get("tools", [])))
    allowed = (requested & readonly) or readonly
    runtime_names = _runtime_tool_names()
    return [tool for tool in TOOLS if tool.get("name") in allowed and tool.get("name") in runtime_names]


async def _default_runtime_subagent_runner(prompt: str, **kwargs) -> str:
    return await run_subagent(prompt=prompt, **kwargs)


async def handle_spawn_agent(
    name: str,
    role: str,
    goal: str,
    lifetime: str = "temporary",
    reason: str = "",
    tools: list | None = None,
    capabilities: list | None = None,
    task_scope: dict | None = None,
    expected_output: dict | None = None,
    mcp_servers: list | None = None,
    blocked_capabilities: list | None = None,
    risk_level: str = "medium",
    ttl_seconds: int | None = None,
    run_now: bool = False,
) -> str:
    """Create a run-scoped temporary Agent from inside the active Lead run."""
    context = get_runtime_context()
    thread_id = str(context.get("thread_id") or "").strip()
    workspace_dir = str(context.get("workspace_dir") or "").strip()
    event_store = None

    if not thread_id or not workspace_dir:
        return "Error: spawn_agent requires active run context with thread_id and workspace_dir."

    cleaned_name = str(name or "").strip()
    cleaned_role = str(role or "").strip()
    cleaned_goal = str(goal or "").strip()
    if not cleaned_name or not cleaned_role or not cleaned_goal:
        return "Error: spawn_agent requires non-empty name, role, and goal."

    if str(lifetime or "temporary").strip().lower() not in {"temporary", "temp", "ephemeral"}:
        return "Error: runtime spawn_agent currently supports temporary agents only."

    spec = {
        "name": cleaned_name,
        "role": cleaned_role,
        "goal": cleaned_goal,
        "reason": str(reason or f"Lead requested a specialist for: {cleaned_goal}"),
        "parent_agent": str(context.get("agent") or "Lead"),
        "tools": _as_string_list(tools),
        "capabilities": _as_string_list(capabilities),
        "mcp_servers": _as_string_list(mcp_servers),
        "blocked_capabilities": _as_string_list(blocked_capabilities),
        "risk_level": str(risk_level or "medium"),
    }
    if isinstance(task_scope, dict):
        spec["task_scope"] = task_scope
    if isinstance(expected_output, dict):
        spec["expected_output"] = expected_output
    if ttl_seconds is not None:
        try:
            spec["ttl_seconds"] = max(60, int(ttl_seconds))
        except (TypeError, ValueError):
            return "Error: ttl_seconds must be an integer."

    run_result: dict[str, Any] | None = None
    try:
        from src.api.services.event_store import get_event_store
        from src.api.services.ephemeral_agent_service import spawn_ephemeral_agent

        event_store = get_event_store()
        event_store.append_event(
            thread_id,
            "agent_spawn_requested",
            title=f"请求创建临时 Agent：{cleaned_name}",
            content=spec["reason"],
            agent="lead",
            payload={"request": spec},
            workspace_dir=workspace_dir,
        )
        agent = spawn_ephemeral_agent(thread_id, spec, workspace_dir)
        event_store.append_event(
            thread_id,
            "agent_spawn_approved",
            title=f"临时 Agent 已创建：{agent.get('name')}",
            content=agent.get("goal", ""),
            agent="lead",
            payload={"agent": agent},
            workspace_dir=workspace_dir,
        )
        if run_now:
            from src.agent.agent_pool import get_or_create_pool

            pool = get_or_create_pool(thread_id)
            # Set status callback from runtime context if available
            _pool_cb = context.get("pool_status_callback")
            if _pool_cb and not pool._status_callback:
                pool.set_status_callback(_pool_cb)
            agent_tools = _tools_for_runtime_agent(agent)
            runner = context.get("subagent_runner") or _default_runtime_subagent_runner

            handle = await pool.submit(
                name=cleaned_name,
                role=cleaned_role,
                goal=str(context.get("prompt") or cleaned_goal),
                runner=runner,
                tools=agent_tools,
            )
            agent["pool_agent_id"] = handle.agent_id
            agent["status"] = "running"
    except ValueError as exc:
        if event_store is not None:
            event_store.append_event(
                thread_id,
                "agent_spawn_rejected",
                title=f"临时 Agent 创建被拒绝：{cleaned_name}",
                content=str(exc),
                agent="lead",
                payload={"request": spec, "reason": str(exc)},
                workspace_dir=workspace_dir,
            )
        return f"Error: {exc}"

    result_payload = {
        "ok": True,
        "agent_id": agent.get("agent_id"),
        "name": agent.get("name"),
        "role": agent.get("role"),
        "status": agent.get("status"),
        "tools": agent.get("tools", []),
        "capabilities": agent.get("capabilities", []),
        "task_scope": agent.get("task_scope", {}),
        "expected_output": agent.get("expected_output", {}),
        "run_now": bool(run_now),
    }
    if run_now:
        result_payload["pool_agent_id"] = agent.get("pool_agent_id")
        result_payload["message"] = "Agent submitted to execution pool. Use gather_agents to collect results."
    else:
        result_payload["result"] = run_result.get("result") if isinstance(run_result, dict) else {}
        result_payload["message"] = "Temporary Agent created for this run."
    return json.dumps(result_payload, ensure_ascii=False)


TOOL_HANDLERS["spawn_agent"] = _safe_handler(
    ["name", "role", "goal"],
    lambda name, role, goal, lifetime="temporary", reason="", tools=None, capabilities=None,
    task_scope=None, expected_output=None, mcp_servers=None, blocked_capabilities=None,
    risk_level="medium", ttl_seconds=None, run_now=False: handle_spawn_agent(
        name=name,
        role=role,
        goal=goal,
        lifetime=lifetime,
        reason=reason,
        tools=tools,
        capabilities=capabilities,
        task_scope=task_scope,
        expected_output=expected_output,
        mcp_servers=mcp_servers,
        blocked_capabilities=blocked_capabilities,
        risk_level=risk_level,
        ttl_seconds=ttl_seconds,
        run_now=run_now,
    ),
)


async def handle_gather_agents(agent_ids: list[str] | None = None) -> str:
    """Wait for spawned agents to complete and return their results."""
    context = get_runtime_context()
    thread_id = str(context.get("thread_id") or "").strip()
    if not thread_id:
        return "Error: gather_agents requires active run context."

    from src.agent.agent_pool import get_pool
    pool = get_pool(thread_id)
    if not pool:
        return "Error: No agent pool found for this run. No agents have been spawned."

    results = await pool.gather(agent_ids)

    output = []
    for aid, handle in results.items():
        entry = {
            "agent_id": aid,
            "name": handle.name,
            "role": handle.role,
            "status": handle.status,
        }
        if handle.result:
            entry["result"] = handle.result[:5000]
        if handle.error:
            entry["error"] = handle.error
        output.append(entry)

    return json.dumps({"ok": True, "agents": output}, ensure_ascii=False)


TOOL_HANDLERS["gather_agents"] = _safe_handler(
    [],
    lambda agent_ids=None: handle_gather_agents(agent_ids=agent_ids),
)


# ========== 系统提示构建器 ==========
from src.agent.prompt_builder import DYNAMIC_BOUNDARY, SystemPromptBuilder


# ========== 上下文压缩器 ==========
from src.agent.compaction import auto_compact, micro_compact

OUTPUT_DIR = get_workdir() / ".task_outputs"
TRANSCRIPTS_DIR = get_workdir() / ".transcripts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

# ========== 错误恢复 ==========
MAX_RECOVERY_ATTEMPTS = 3
BACKOFF_BASE_DELAY = 1.0
CONTINUATION_MESSAGE = "Output limit reached. Please continue directly."


def _block_value(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _assistant_tool_use_ids(message: dict[str, Any]) -> list[str]:
    if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
        return []
    ids: list[str] = []
    for block in message["content"]:
        if _block_value(block, "type") == "tool_use":
            tool_id = _block_value(block, "id")
            if tool_id:
                ids.append(str(tool_id))
    return ids


def _is_tool_result_block(block: Any) -> bool:
    return _block_value(block, "type") == "tool_result"


def _synthetic_tool_result(tool_use_id: str) -> dict[str, str]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": "Tool result was missing from recovered history; continue from the available context.",
    }


def _ensure_tool_result_sequence(messages: list) -> list:
    """Repair Anthropic tool-use history so every tool_use has an immediate result."""
    repaired: list = []
    index = 0
    while index < len(messages):
        message = messages[index]
        msg_dict = message if isinstance(message, dict) else {"role": getattr(message, "role", "user"), "content": getattr(message, "content", "")}
        tool_ids = _assistant_tool_use_ids(msg_dict)
        repaired.append(msg_dict)
        if not tool_ids:
            index += 1
            continue

        next_msg = messages[index + 1] if index + 1 < len(messages) else None
        next_dict = (
            next_msg
            if isinstance(next_msg, dict)
            else {"role": getattr(next_msg, "role", "user"), "content": getattr(next_msg, "content", "")}
            if next_msg is not None
            else None
        )
        next_content = next_dict.get("content") if isinstance(next_dict, dict) else None

        if isinstance(next_dict, dict) and next_dict.get("role") == "user" and isinstance(next_content, list):
            by_id = {
                str(_block_value(block, "tool_use_id")): block
                for block in next_content
                if _is_tool_result_block(block) and _block_value(block, "tool_use_id")
            }
            ordered_results = [by_id.get(tool_id) or _synthetic_tool_result(tool_id) for tool_id in tool_ids]
            extra_blocks = [
                block
                for block in next_content
                if not (_is_tool_result_block(block) and str(_block_value(block, "tool_use_id")) in set(tool_ids))
            ]
            repaired.append({**next_dict, "content": ordered_results + extra_blocks})
            index += 2
            continue

        repaired.append({"role": "user", "content": [_synthetic_tool_result(tool_id) for tool_id in tool_ids]})
        index += 1

    return repaired

def backoff_delay(attempt: int) -> float:
    import random
    delay = BACKOFF_BASE_DELAY * (2 ** attempt) + random.random()
    return min(delay, 30.0)


def _is_retryable_error(exc: Exception) -> bool:
    """Check if an LLM API error is retryable (transient)."""
    from anthropic import RateLimitError, InternalServerError, APIConnectionError, APITimeoutError
    return isinstance(exc, (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError))


async def _retryable_llm_call(client, **kwargs):
    """Call LLM API with retry for transient errors."""
    import asyncio as _asyncio
    last_exc = None
    for attempt in range(MAX_RECOVERY_ATTEMPTS):
        try:
            return await client.messages.create(**kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_error(exc) or attempt == MAX_RECOVERY_ATTEMPTS - 1:
                raise
            delay = backoff_delay(attempt)
            logger.warning(
                "LLM 调用失败，准备重试 attempt=%s/%s delay=%.1fs error=%s",
                attempt + 1,
                MAX_RECOVERY_ATTEMPTS,
                delay,
                exc,
            )
            await _asyncio.sleep(delay)
    raise last_exc

# ========== 后台任务管理器 ==========
from src.agent.managers import BackgroundManager as _BgMgr

_bg_manager = None

def get_background_manager() -> _BgMgr:
    global _bg_manager
    if _bg_manager is None:
        _bg_manager = _BgMgr(workdir=get_workdir())
    return _bg_manager


def _tool_policy_allows(decision: Any) -> tuple[bool, str]:
    if decision is None:
        return True, ""
    if isinstance(decision, dict):
        return bool(decision.get("allowed", True)), str(decision.get("reason", ""))
    return bool(getattr(decision, "allowed", True)), str(getattr(decision, "reason", ""))


async def _call_tool_check(callback: Callable[[str, dict], Any] | None, tool_name: str, tool_input: dict) -> Any:
    """Run a sync or async tool-policy callback."""
    if not callback:
        return None
    decision = callback(tool_name, tool_input)
    if inspect.isawaitable(decision):
        return await decision
    return decision


async def _raise_if_cancelled(callback: Callable[[], Any] | None) -> None:
    """Stop the Agent loop at safe checkpoints when the host requests cancel."""
    if not callback:
        return
    result = callback()
    if inspect.isawaitable(result):
        result = await result
    if result:
        raise WorkflowCancelledError("工作流已被用户取消")


# ========== 主 Agent Loop ==========
async def agent_loop(
    messages: list,
    system: str,
    tools: list = None,
    max_turns: int = 100,
    on_tool_check: Callable[[str, dict], Any] = None,
    on_tool_call: Callable[[str, dict, str], None] = None,
    on_llm_response: Callable[[int, int], None] = None,
    on_cancel_check: Callable[[], Any] = None,
    session_id: str = None,
    runtime_context: dict[str, Any] | None = None,
) -> str:
    """
    统一 agent loop - 整合所有增强功能
    """
    if tools is None:
        tools = ALL_TOOLS

    # 自动上下文压缩
    messages = auto_compact(messages)

    client = create_client()

    try:
        with bind_runtime_context(runtime_context):
            for turn in range(max_turns):
                try:
                    await _raise_if_cancelled(on_cancel_check)
                    _llm_start = _metrics.record_llm_call_start()
                    messages = _ensure_tool_result_sequence(messages)
                    resp = await _retryable_llm_call(
                        client,
                        model=get_model_name(),
                        system=system,
                        messages=messages,
                        tools=tools,
                        max_tokens=LLM_MAX_TOKENS,
                        temperature=LLM_TEMPERATURE,
                    )
                    _metrics.record_llm_call_end(_llm_start, input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
                    if on_llm_response:
                        on_llm_response(resp.usage.input_tokens, resp.usage.output_tokens)

                    messages.append({"role": "assistant", "content": _content_to_dict(resp.content)})

                    await _raise_if_cancelled(on_cancel_check)
                    if resp.stop_reason != "tool_use":
                        return "".join(b.text for b in resp.content if hasattr(b, "text"))

                    # 处理工具调用：只读工具并行，写工具串行（带文件锁）
                    _ctx = get_runtime_context()
                    _fl = _ctx.get("file_lock")
                    tool_calls = [
                        (block.name, block.input, block.id)
                        for block in resp.content if block.type == "tool_use"
                    ]
                    parallel_calls = [(n, i, tid) for n, i, tid in tool_calls if n in PARALLEL_TOOLS]
                    sequential_calls = [(n, i, tid) for n, i, tid in tool_calls if n not in PARALLEL_TOOLS]

                    tool_results = [None] * len(tool_calls)

                    # 并行执行只读工具
                    if parallel_calls:
                        parallel_tasks = [
                            _execute_single_tool(n, i, tid, on_tool_check, on_tool_call, on_cancel_check, session_id, file_lock=_fl)
                            for n, i, tid in parallel_calls
                        ]
                        parallel_results = await asyncio.gather(*parallel_tasks, return_exceptions=True)
                        for idx, (_, _, tid) in enumerate(parallel_calls):
                            result = parallel_results[idx]
                            if isinstance(result, Exception):
                                result = {"type": "tool_result", "tool_use_id": tid, "content": f"Error: {result}"}
                            orig_idx = next(j for j, (_, _, t) in enumerate(tool_calls) if t == tid)
                            tool_results[orig_idx] = result

                    # 串行执行写工具
                    for n, i, tid in sequential_calls:
                        result = await _execute_single_tool(n, i, tid, on_tool_check, on_tool_call, on_cancel_check, session_id, file_lock=_fl)
                        orig_idx = next(j for j, (_, _, t) in enumerate(tool_calls) if t == tid)
                        tool_results[orig_idx] = result

                    tool_results = [r for r in tool_results if r is not None]

                    messages.append({"role": "user", "content": tool_results})

                    # 每轮自动压缩
                    messages = auto_compact(messages)

                except WorkflowCancelledError:
                    raise
                except Exception as e:
                    error_str = str(e)
                    if "max_tokens" in error_str.lower():
                        messages.append({"role": "user", "content": CONTINUATION_MESSAGE})
                        continue
                    else:
                        return f"Error: {e}"

            return "(max turns reached)"
    finally:
        await client.close()


# ========== 流式 Agent Loop ==========

class RunSession:
    """Per-run isolated state container to prevent cross-run contamination."""
    def __init__(self, session_id: str = "", workdir: Path = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.workdir = workdir or get_workdir()
        self.todo_manager: Optional[TodoManager] = None
        self.task_manager: Optional[TaskManager] = None
        self.created_at: float = time.time()

    @property
    def todo(self) -> "TodoManager":
        if self.todo_manager is None:
            self.todo_manager = TodoManager()
        return self.todo_manager

    @property
    def tasks(self) -> "TaskManager":
        if self.task_manager is None:
            self.task_manager = TaskManager(self.workdir / ".tasks")
        return self.task_manager


_runs: dict[str, RunSession] = {}

def get_run_session(session_id: str, workdir: Path = None) -> RunSession:
    """Get or create a per-run isolated session."""
    if session_id not in _runs:
        _runs[session_id] = RunSession(session_id, workdir)
    return _runs[session_id]

def cleanup_run_session(session_id: str):
    """Remove run session after completion."""
    _runs.pop(session_id, None)


async def agent_loop_stream(
    messages: list,
    system: str,
    tools: list = None,
    max_turns: int = 100,
    on_tool_check: Callable[[str, dict], Any] = None,
    on_tool_call: Callable[[str, dict, str], None] = None,
    on_cancel_check: Callable[[], Any] = None,
    session_id: str = None,
    runtime_context: dict[str, Any] | None = None,
):
    """
    Streaming agent loop - yields events as they happen.

    Yield types:
        ("token", text)        - streaming text token
        ("tool_start", name)   - tool call started
        ("tool_input", input)  - tool input received
        ("tool_result", result)- tool execution result
        ("done", full_text)    - final response ready
        ("error", message)     - error occurred
        ("metrics", inp, out)  - token usage update
    """
    if tools is None:
        tools = ALL_TOOLS

    messages = auto_compact(messages)
    client = create_client()
    context_token = _RUNTIME_CONTEXT.set(dict(runtime_context or {}))

    try:
        for turn in range(max_turns):
            try:
                await _raise_if_cancelled(on_cancel_check)
                _llm_start = _metrics.record_llm_call_start()
                messages = _ensure_tool_result_sequence(messages)

                # Use streaming API
                stream = await _retryable_llm_call(
                    client,
                    model=get_model_name(),
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=LLM_MAX_TOKENS,
                    temperature=LLM_TEMPERATURE,
                    stream=True,
                )

                # Accumulate streaming response
                text_blocks: list[str] = []
                assistant_content: list[dict[str, Any]] = []
                tool_blocks: list[dict] = []
                current_block_type: str = ""
                current_text: str = ""
                current_tool_name: str = ""
                current_tool_input: str = ""
                current_tool_id: str = ""
                input_tokens = 0
                output_tokens = 0
                stop_reason = ""

                async for event in stream:
                    event_type = getattr(event, 'type', '')

                    if event_type == "message_start":
                        if hasattr(event, 'message') and hasattr(event.message, 'usage'):
                            input_tokens = event.message.usage.input_tokens

                    elif event_type == "content_block_start":
                        block = event.content_block
                        current_block_type = block.type
                        if block.type == "text":
                            current_text = block.text or ""
                            text_blocks.append(current_text)
                            if current_text:
                                yield ("token", current_text)
                        elif block.type == "tool_use":
                            current_tool_name = block.name
                            current_tool_id = block.id
                            current_tool_input = ""
                            yield ("tool_start", current_tool_name)
                        elif block.type == "thinking":
                            pass  # Don't stream thinking content

                    elif event_type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            current_text += delta.text
                            text_blocks.append(delta.text)
                            yield ("token", delta.text)
                        elif delta.type == "input_json_delta":
                            current_tool_input += delta.partial_json
                        elif delta.type == "thinking_delta":
                            pass

                    elif event_type == "content_block_stop":
                        if current_block_type == "text" and current_text:
                            assistant_content.append({"type": "text", "text": current_text})
                        elif current_block_type == "tool_use" and current_tool_id:
                            try:
                                parsed_input = json.loads(current_tool_input) if current_tool_input else {}
                            except json.JSONDecodeError:
                                parsed_input = {}
                            tool_block = {
                                "id": current_tool_id,
                                "name": current_tool_name,
                                "input": parsed_input,
                            }
                            tool_blocks.append(tool_block)
                            assistant_content.append({
                                "type": "tool_use",
                                "id": tool_block["id"],
                                "name": tool_block["name"],
                                "input": tool_block["input"],
                            })
                            yield ("tool_input", current_tool_name, parsed_input)

                    elif event_type == "message_delta":
                        stop_reason = event.delta.stop_reason or ""
                        if hasattr(event, 'usage'):
                            output_tokens = event.usage.output_tokens

                _metrics.record_llm_call_end(_llm_start, input_tokens=input_tokens, output_tokens=output_tokens)
                yield ("metrics", input_tokens, output_tokens)

                # Build assistant message for conversation history
                full_text = "".join(text_blocks)
                messages.append({"role": "assistant", "content": assistant_content})

                await _raise_if_cancelled(on_cancel_check)
                if stop_reason != "tool_use":
                    yield ("done", full_text)
                    return

                # Process tool calls: read-only tools parallel, write tools sequential (with file lock)
                _ctx = get_runtime_context()
                _fl = _ctx.get("file_lock")
                parallel_calls = [tb for tb in tool_blocks if tb["name"] in PARALLEL_TOOLS]
                sequential_calls = [tb for tb in tool_blocks if tb["name"] not in PARALLEL_TOOLS]

                tool_results = [None] * len(tool_blocks)
                tb_by_id = {tb["id"]: i for i, tb in enumerate(tool_blocks)}

                # Parallel execution of read-only tools
                if parallel_calls:
                    parallel_tasks = [
                        _execute_single_tool(tb["name"], tb["input"], tb["id"], on_tool_check, on_tool_call, on_cancel_check, session_id, file_lock=_fl)
                        for tb in parallel_calls
                    ]
                    parallel_results = await asyncio.gather(*parallel_tasks, return_exceptions=True)
                    for idx, tb in enumerate(parallel_calls):
                        result = parallel_results[idx]
                        if isinstance(result, Exception):
                            result = {"type": "tool_result", "tool_use_id": tb["id"], "content": f"Error: {result}"}
                        tool_results[tb_by_id[tb["id"]]] = result
                        yield ("tool_result", tb["name"], tb["input"], result["content"])

                # Sequential execution of write tools
                for tb in sequential_calls:
                    result = await _execute_single_tool(tb["name"], tb["input"], tb["id"], on_tool_check, on_tool_call, on_cancel_check, session_id, file_lock=_fl)
                    tool_results[tb_by_id[tb["id"]]] = result
                    yield ("tool_result", tb["name"], tb["input"], result["content"])

                tool_results = [r for r in tool_results if r is not None]

                messages.append({"role": "user", "content": tool_results})
                messages = auto_compact(messages)

            except WorkflowCancelledError:
                raise
            except Exception as e:
                error_str = str(e)
                if "max_tokens" in error_str.lower():
                    messages.append({"role": "user", "content": CONTINUATION_MESSAGE})
                    continue
                yield ("error", str(e))
                return

        yield ("done", "(max turns reached)")
        return
    finally:
        _RUNTIME_CONTEXT.reset(context_token)
        await client.close()


# ========== 导出 ==========
__all__ = [
    "agent_loop", "agent_loop_stream", "run_subagent",
    "RunSession", "get_run_session", "cleanup_run_session",
    "BASE_TOOLS", "TASK_TOOLS", "AGENT_RUNTIME_TOOLS", "ALL_TOOLS", "TOOLS",
    "TOOL_HANDLERS", "handle_spawn_agent", "bind_runtime_context", "get_runtime_context", "get_todo_manager",
    "TodoManager", "TaskManager", "BackgroundManager",
    "SystemPromptBuilder", "DYNAMIC_BOUNDARY",
    "auto_compact", "micro_compact", "backoff_delay",
    "get_background_manager",
    "create_client", "get_workdir", "MODEL",
]
