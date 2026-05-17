#!/usr/bin/env python3
"""
nanoCursor Core Engine - 统一 MVP 引擎

整合所有借鉴的模块：
- base_engine: while loop + tool_use
- core_engine: Todo/Task/Subagent 工具
- system_prompt_builder: sections 管道式提示
- context_compactor: 三层上下文压缩
- error_recovery: 错误恢复策略
- hook_manager: 事件钩子系统
- background_manager: 后台任务管理
- cron_scheduler: 定时任务调度
- worktree_manager: git worktree 隔离
- skill_registry: 技能按需加载
- permission_manager: 权限管道
- memory_manager: 跨会话记忆
"""

import os
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv(override=False)

from src.infra.llm_config import MODEL, API_KEY, BASE_URL, create_client
from src.infra.metrics import metrics as _metrics

# ========== 配置 ==========
import src.infra.config as _config
from src.infra.config import LLM_MAX_TOKENS, LLM_TEMPERATURE

def get_workdir() -> Path:
    """Return the current workspace directory (always reads latest value from config)."""
    return Path(_config.WORKSPACE_DIR).resolve()


def reset_runtime_caches() -> None:
    """Clear workspace-scoped runtime singletons after the active workspace changes."""
    global _todo_mgr
    _todo_mgr = None

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

TODO_TOOLS = [
    {"name": "TodoWrite", "description": "Add/update todos",
     "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string"}}}}}}},
    {"name": "TodoList", "description": "List all todos",
     "input_schema": {"type": "object", "properties": {}}},
]

TASK_TOOLS = [
    {"name": "task_create", "description": "Create a task",
     "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}, "blocked_by": {"type": "array", "items": {"type": "string"}}}, "required": ["subject"]}},
    {"name": "task_update", "description": "Update task status",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "status": {"type": "string"}}, "required": ["task_id", "status"]}},
    {"name": "task_list", "description": "List tasks",
     "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}}},
]

SUBAGENT_TOOLS = [
    {"name": "task", "description": "Spawn a subagent with fresh context",
     "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}, "description": {"type": "string"}, "agent_type": {"type": "string"}}, "required": ["prompt"]}},
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

# Team tools from team.py
from src.team.team import (
    get_team_manager, BUS, REQUEST_STORE,
    scan_unclaimed_tasks, claim_task,
    handle_shutdown_request as _handle_shutdown_req, handle_plan_review as _handle_plan_review, check_request_status as _check_request_status,
    make_identity_block, ensure_identity_context,
    TEAM_TOOLS as _TEAM_TOOLS,
)

# Add team tools to ALL_TOOLS
# Project tools
from src.tools.project_tools import PROJECT_TOOLS as _PROJECT_TOOLS
# Git tools
from src.tools.git_tools import GIT_TOOLS as _GIT_TOOLS

ALL_TOOLS = BASE_TOOLS + TODO_TOOLS + TASK_TOOLS + SUBAGENT_TOOLS + MEMORY_TOOLS + _PROJECT_TOOLS + _GIT_TOOLS + _TEAM_TOOLS
# Alias for backwards compatibility
TOOLS = ALL_TOOLS


# ========== 工具处理函数 ==========
import subprocess

def safe_path(p: str) -> Path:
    root = get_workdir()
    normalized = str(p).replace("\\", os.sep)
    path = (Path(normalized) if Path(normalized).is_absolute() else root / normalized).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return f"Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=get_workdir(), capture_output=True, timeout=120)
        try:
            out = r.stdout.decode('gbk', errors='replace') + r.stderr.decode('gbk', errors='replace')
        except (UnicodeDecodeError, AttributeError):
            out = (r.stdout or b'') + (r.stderr or b'')
            if isinstance(out, bytes):
                out = out.decode('utf-8', errors='replace')
        return out.strip()[:50000] or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except FileNotFoundError:
        return f"Error: Command not found: {command.split()[0] if command else ''}"
    except Exception as e:
        return f"Error: {e}"


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
            cmd.extend(["-v", "--tb=short", "--timeout=60"])
            r = subprocess.run(cmd, cwd=str(wd), capture_output=True, timeout=120)
        elif framework == "npm":
            cmd = ["npm", "test"]
            if test_path_arg:
                cmd.extend(["--", test_path_arg])
            r = subprocess.run(cmd, cwd=str(wd), capture_output=True, timeout=120)
        elif framework == "go":
            cmd = ["go", "test", "./..."]
            if test_path_arg:
                cmd = ["go", "test", test_path_arg]
            r = subprocess.run(cmd, cwd=str(wd), capture_output=True, timeout=120)
        else:
            # Generic: just try pytest
            cmd = ["python", "-m", "pytest", "tests/", "-v", "--tb=short"]
            r = subprocess.run(cmd, cwd=str(wd), capture_output=True, timeout=120)
            framework = "pytest"

        try:
            stdout = r.stdout.decode('utf-8', errors='replace')
            stderr = r.stderr.decode('utf-8', errors='replace')
        except Exception:
            stdout = str(r.stdout) if r.stdout else ""
            stderr = str(r.stderr) if r.stderr else ""

        output = stdout + stderr
        output = output[:8000]  # Limit output size

        # Parse pytest-style summary
        passed = 0
        failed = 0
        errors = 0
        summary_line = ""
        for line in output.splitlines():
            if "passed" in line and ("failed" in line or "error" in line):
                summary_line = line.strip()
                import re as _re
                m = _re.search(r'(\d+)\s+passed', line)
                passed = int(m.group(1)) if m else 0
                m = _re.search(r'(\d+)\s+failed', line)
                failed = int(m.group(1)) if m else 0
                m = _re.search(r'(\d+)\s+errors?', line)
                errors = int(m.group(1)) if m else 0

        if r.returncode == 0:
            return f"All {passed} tests passed. ✓\n{summary_line}"
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
                f"Exit code: {r.returncode}\n"
                f"{summary_line}\n\n"
                f"Failure details:\n{failure_detail}"
            )
    except FileNotFoundError:
        return f"Test framework '{framework}' not found. Install it or specify a different framework."
    except subprocess.TimeoutExpired:
        return "Tests timed out after 120s."
    except Exception as e:
        return f"Error running tests: {e}"


def auto_verify_file(path: Path) -> str:
    """Automatically verify a file after writing/editing. Returns '' if OK, error message if not."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".py":
            import py_compile
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as e:
                return f"Python syntax error in {path.name}: {e}"
        elif suffix in (".js", ".mjs"):
            r = subprocess.run(
                ["node", "--check", str(path)],
                capture_output=True, timeout=10,
            )
            if r.returncode != 0:
                err = (r.stderr or r.stdout)
                if isinstance(err, bytes):
                    err = err.decode('utf-8', errors='replace')
                return f"JavaScript syntax error in {path.name}: {err[:500]}"
        elif suffix == ".ts":
            # Check if tsc or esbuild is available
            r = subprocess.run(
                ["npx", "esbuild", str(path), "--format=esm"],
                capture_output=True, timeout=15,
            )
            if r.returncode != 0:
                err = r.stderr or r.stdout
                if isinstance(err, bytes):
                    err = err.decode('utf-8', errors='replace')
                return f"TypeScript error in {path.name}: {err[:500]}"
        elif suffix in (".json",):
            import json
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                return f"JSON syntax error in {path.name}: {e}"
    except FileNotFoundError:
        pass  # Tool not available - skip verification
    except Exception:
        pass  # Verification failure shouldn't block
    return ""

def run_read(path: str, limit: int = None) -> str:
    try:
        content = safe_path(path).read_text(encoding="utf-8")
        if limit:
            lines = content.splitlines()
            if len(lines) > limit:
                content = "\n".join(lines[:limit]) + f"\n... ({len(lines) - limit} more lines)"
        return content[:50000]
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        existed = fp.exists()
        fp.write_text(content, encoding="utf-8")
        # Auto-verify after write
        verify_result = auto_verify_file(fp)
        result = f"{'Updated' if existed else 'Created'} {path} ({len(content)} bytes)"
        if verify_result:
            result += f"\n⚠️  {verify_result}"
        return result
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str = "", new_text: str = "", start_line: int = None, end_line: int = None) -> str:
    """Edit file - supports both string-based (legacy) and line-based replacement.

    Line-based mode (preferred): provide start_line, end_line, new_text.
    String-based mode (legacy): provide old_text, new_text.

    Returns a diff summary of what changed.
    """
    import difflib
    try:
        fp = safe_path(path)
        if not fp.exists():
            return f"Error: File not found: {path}"
        content = fp.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        if start_line is not None and end_line is not None:
            # Line-based mode (1-indexed)
            if start_line < 1 or end_line > len(lines) or start_line > end_line:
                return f"Error: Invalid line range {start_line}-{end_line} (file has {len(lines)} lines)"
            old_slice = "".join(lines[start_line-1:end_line])
            new_lines = new_text.splitlines(keepends=True)
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines[-1] += "\n"
            new_slice = "".join(new_lines)
            new_content_lines = lines[:start_line-1] + new_lines + lines[end_line:]
            new_content = "".join(new_content_lines)
        elif old_text:
            # String-based mode (legacy fallback)
            if old_text not in content:
                # Try fuzzy match across line boundaries
                return f"Error: Text not found in file. Use start_line/end_line for line-based edits, or verify the exact text."
            old_slice = old_text
            new_slice = new_text
            new_content = content.replace(old_text, new_text, 1)
        else:
            return "Error: Provide either (old_text, new_text) or (start_line, end_line, new_text)"

        fp.write_text(new_content, encoding="utf-8")

        # Auto-verify after edit
        verify_result = auto_verify_file(fp)

        # Generate diff summary
        old_display = old_slice[:500]
        new_display = new_slice[:500]
        diff_lines = list(difflib.unified_diff(
            old_slice.splitlines(keepends=True),
            new_slice.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
            lineterm="",
        ))
        diff_text = "\n".join(diff_lines[:30])

        if start_line is not None:
            loc = f"lines {start_line}-{end_line}"
        else:
            loc = "matched text"
        added = len(new_slice) - len(old_slice)
        change = f"+{added}" if added >= 0 else str(added)
        result = f"Edited {path} ({loc}, {change} chars)\n```diff\n{diff_text}\n```"
        if verify_result:
            result += f"\n⚠️  {verify_result}"
        return result
    except Exception as e:
        return f"Error: {e}"


# ========== Todo 管理器 ==========

def _todo_file() -> Path:
    return get_workdir() / ".todos.json"

@dataclass
class TodoItem:
    id: str
    content: str
    status: str = "pending"
    created_at: float = field(default_factory=time.time)

class TodoManager:
    def __init__(self):
        self.items: list[TodoItem] = []
        self._load()

    def _load(self):
        todo_file = _todo_file()
        if todo_file.exists():
            try:
                data = json.loads(todo_file.read_text(encoding="utf-8"))
                self.items = [TodoItem(**t) for t in data]
            except (json.JSONDecodeError, TypeError, OSError):
                self.items = []

    def _save(self):
        data = [{"id": t.id, "content": t.content, "status": t.status, "created_at": t.created_at} for t in self.items]
        _todo_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def add(self, content: str) -> str:
        todo_id = str(int(time.time() * 1000))
        self.items.append(TodoItem(id=todo_id, content=content))
        self._save()
        return todo_id

    def update(self, todo_id: str, status: str):
        for t in self.items:
            if t.id == todo_id:
                t.status = status
                break
        self._save()

    def list_all(self) -> list[TodoItem]:
        return self.items


# ========== 任务管理器 ==========
class TaskManager:
    def __init__(self, tasks_dir: Path = None):
        self.tasks_dir = (tasks_dir or get_workdir() / ".tasks")
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def _task_file(self, task_id: str) -> Path:
        return self.tasks_dir / f"task_{task_id}.json"

    def create(self, subject: str, description: str = "", blocked_by: list = None) -> dict:
        task_id = str(int(time.time() * 1000))
        task = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "blocked_by": blocked_by or [],
            "created_at": time.time(),
            "completed_at": None,
        }
        self._task_file(task_id).write_text(json.dumps(task, ensure_ascii=False))
        return task

    def get(self, task_id: str) -> Optional[dict]:
        f = self._task_file(task_id)
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return None

    def update_status(self, task_id: str, status: str):
        task = self.get(task_id)
        if task:
            task["status"] = status
            if status == "completed":
                task["completed_at"] = time.time()
            self._task_file(task_id).write_text(json.dumps(task, ensure_ascii=False))

    def list_all(self) -> list[dict]:
        tasks = []
        for f in self.tasks_dir.glob("task_*.json"):
            try:
                tasks.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return sorted(tasks, key=lambda t: t.get("created_at", 0))

    def list_runnable(self) -> list[dict]:
        all_tasks = self.list_all()
        completed_ids = {t["id"] for t in all_tasks if t["status"] == "completed"}
        runnable = []
        for t in all_tasks:
            if t["status"] != "pending":
                continue
            blocked_by = t.get("blocked_by", [])
            if all(b in completed_ids for b in blocked_by):
                runnable.append(t)
        return runnable


# ========== 子代理 ==========
async def run_subagent(prompt: str, system: str = None, agent_type: str = "Explore") -> str:
    if system is None:
        system = f"You are a {agent_type} subagent at {get_workdir()}. Complete the task and summarize."

    client = create_client()
    messages = [{"role": "user", "content": prompt}]

    try:
        for turn in range(30):
            resp = await client.messages.create(
                model=MODEL, system=system, messages=messages,
                tools=BASE_TOOLS, max_tokens=LLM_MAX_TOKENS,
            )
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                break

            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    handler = TOOL_HANDLERS.get(block.name, lambda **kw: f"Unknown: {block.name}")
                    output = str(handler(**block.input))[:50000]
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
            messages.append({"role": "user", "content": results})

        return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no summary)"
    finally:
        await client.close()


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
    "list_directory": lambda **kw: run_bash(f'dir /b "{kw.get("path", ".")}" 2>nul'),
    "run_tests": lambda test_path="", framework="auto", **kw: run_tests(test_path, framework),
}

# Todo handlers
_todo_mgr = None
def get_todo_manager() -> TodoManager:
    global _todo_mgr
    if _todo_mgr is None:
        _todo_mgr = TodoManager()
    return _todo_mgr

def handle_TodoWrite(items: list) -> str:
    tm = get_todo_manager()
    results = []
    for item in items:
        content = item.get("content", "")
        status = item.get("status", "pending")
        if status == "completed":
            for t in tm.items:
                if t.content == content:
                    tm.update(t.id, "completed")
                    results.append(f"Completed: {content}")
                    break
        else:
            todo_id = tm.add(content)
            results.append(f"Added: {content} (id={todo_id})")
    return "\n".join(results)

def handle_TodoList() -> str:
    tm = get_todo_manager()
    if not tm.items:
        return "No todos"
    lines = []
    for t in tm.items:
        status_icon = "✓" if t.status == "completed" else "○"
        lines.append(f"[{status_icon}] {t.content} ({t.status})")
    return "\n".join(lines)

TOOL_HANDLERS["TodoWrite"] = lambda **kw: handle_TodoWrite(kw.get("items", []))
TOOL_HANDLERS["TodoList"] = lambda **kw: handle_TodoList()

# Task handlers
_task_mgr = None
def get_task_manager() -> TaskManager:
    global _task_mgr
    if _task_mgr is None:
        _task_mgr = TaskManager()
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
from src.memory.manager import get_memory_manager as _get_memory_mgr

def handle_add_memory(content: str, category: str, importance: int = 1, tags: list = None) -> str:
    from src.tools.memory_tools import add_memory
    return add_memory(content, category, importance, tags)

def handle_recall_memories(query: str, category: str = None, limit: int = 10) -> str:
    from src.tools.memory_tools import recall_memories
    return recall_memories(query, category, limit)

def handle_update_memory(memory_id: str, content: str = None, importance: int = None) -> str:
    from src.tools.memory_tools import update_memory
    return update_memory(memory_id, content, importance)

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

# Team tool handlers
def handle_spawn_teammate(name: str, role: str, prompt: str, autonomous: bool = True) -> str:
    tm = get_team_manager()
    tid = tm.spawn(name, role, prompt, autonomous)
    return f"Spawned teammate '{name}' (thread={tid})"

def handle_list_teammates() -> str:
    tm = get_team_manager()
    result = tm.list_all()
    return result if result else "No teammates"

def handle_send_message(to: str, content: str, msg_type: str = "message") -> str:
    msg_id = BUS.send("lead", to, content, msg_type)
    return f"Message sent to '{to}', id={msg_id}"

def handle_read_inbox() -> str:
    inbox = BUS.read_inbox("lead")
    if not inbox:
        return "(inbox empty)"
    lines = []
    for msg in inbox:
        lines.append(f"[{msg.get('id', '?')} from {msg.get('from', '?')}]: {msg.get('content', '')}")
    return "\n".join(lines)

def handle_broadcast(content: str) -> str:
    tm = get_team_manager()
    teammates = tm.list_all()
    names = [t["name"] for t in teammates]
    BUS.broadcast("lead", content, names)
    return f"Broadcast to {names}"

def handle_shutdown_request(teammate: str) -> str:
    return _handle_shutdown_req(teammate)

def handle_shutdown_response(request_id: str) -> str:
    status = _check_request_status(request_id)
    if "error" in status:
        return f"Unknown request_id: {request_id}"
    return f"Request {request_id}: {status.get('status', 'unknown')}"

def handle_plan_approval(request_id: str, approve: bool, feedback: str = "") -> str:
    return _handle_plan_review(request_id, approve, feedback)

def handle_claim_task(task_id: str) -> str:
    tm = get_team_manager()
    my_name = tm._index.get("lead", {}).get("name", "lead") if hasattr(tm, '_index') else "lead"
    return claim_task(task_id, my_name, source="teammate")

TOOL_HANDLERS["spawn_teammate"] = _safe_handler(["name", "role", "prompt"], lambda name, role, prompt, autonomous=True: handle_spawn_teammate(name, role, prompt, autonomous))
TOOL_HANDLERS["list_teammates"] = lambda **kw: handle_list_teammates()
TOOL_HANDLERS["send_message"] = _safe_handler(["to", "content"], lambda to, content, msg_type="message": handle_send_message(to, content, msg_type))
TOOL_HANDLERS["read_inbox"] = lambda **kw: handle_read_inbox()
TOOL_HANDLERS["broadcast"] = _safe_handler(["content"], lambda content: handle_broadcast(content))
TOOL_HANDLERS["shutdown_request"] = _safe_handler(["teammate"], lambda teammate: handle_shutdown_request(teammate))
TOOL_HANDLERS["shutdown_response"] = _safe_handler(["request_id"], lambda request_id: handle_shutdown_response(request_id))
TOOL_HANDLERS["plan_approval"] = _safe_handler(["request_id", "approve"], lambda request_id, approve, feedback="": handle_plan_approval(request_id, approve, feedback))
TOOL_HANDLERS["claim_task"] = _safe_handler(["task_id"], lambda task_id: handle_claim_task(task_id))


# ========== 系统提示构建器（借鉴 s10） ==========
DYNAMIC_BOUNDARY = "=== DYNAMIC_BOUNDARY ==="

def _build_core() -> str:
    return f"""你是 nanoCursor AgentHub 的 Lead Agent，一个多 Agent 软件交付工作台的协调者。

【核心原则】

1. **多 Agent 协作优先** — 对于复杂任务，不要自己全干。使用 spawn_teammate 组建团队：
   - Planner: 需求分析和任务拆解
   - Coder: 代码实现和文件修改
   - Tester: 验证和测试
   - Reviewer: 代码审查和风险评估
   - Designer: UI/UX 设计和前端打磨
   给每个 teammate 清晰、具体的任务指令。通过 send_message 跟进进度，用 read_inbox 接收报告。

2. **像人一样对话** — 用户只是聊天时，就自然地聊天，不要调用任何工具。说"你好"你就回"你好！有什么可以帮你的？"，仅此而已。

3. **按需使用工具** — 只有在用户明确要求做编程相关操作时才调用工具。

4. **先思考再行动** — 理解用户真正想要什么。复杂任务先 spawn Planner 做需求分析，再 spawn Coder 实现。

5. **用中文回复** — 始终使用中文与用户交流。

6. **简洁有力** — 用户没说要看代码就不要贴代码，没说要做就不要做。回复尽量简短。

【环境信息】
- 工作目录: {get_workdir()}
- 操作系统: Windows
- Windows 命令: dir (不是 ls), type (不是 cat), del (不是 rm), copy (不是 cp)

【多 Agent 工作流】
对于编程任务，推荐流程：
1. spawn_teammate(name="Planner", role="planner", prompt="分析需求并拆解任务...")
2. 收到 Planner 的任务列表后，用 task_create 创建任务到共享任务板
3. spawn_teammate(name="Coder", role="coder", prompt="实现任务...")
4. spawn_teammate(name="Tester", role="tester", prompt="验证实现...")
5. 通过 read_inbox 接收各 agent 的完成报告
6. 汇总结果回复用户

【验证工作流 - 重要！】
每次文件修改后，你应该：
1. 使用 run_tests 运行项目的测试套件
2. 如果测试失败，分析失败原因，修复代码
3. 再次运行 run_tests 确认修复
4. 测试全部通过后才报告"完成"
5. 如果项目没有测试，至少运行语法检查和导入检查（auto_verify_file 已自动做）
不要报告"完成"除非你已验证代码能运行。

【工具说明】
- bash: 执行 shell 命令
- read_file: 读取文件内容（编辑前先读文件获取准确行号）
- write_file: 创建/覆盖文件
- edit_file: 编辑文件（推荐用 start_line/end_line 行号定位）
- list_directory: 列出目录内容
- project_context / search_codebase: 理解项目结构
- TodoWrite / TodoList: 管理待办事项
- task_create / task_update / task_list: 管理共享任务板
- task: 启动子代理处理独立任务
- spawn_teammate / send_message / broadcast / read_inbox: 多 Agent 团队管理
"""

def _build_tool_listing(tools: list) -> str:
    if not tools:
        return ""
    lines = ["【可用工具】"]
    for t in tools:
        name = t.get("name", "")
        desc = t.get("description", "")
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)

def _build_dynamic_context() -> str:
    from datetime import datetime
    import platform
    return f"""【当前环境】
- 日期: {datetime.now().strftime('%Y-%m-%d')}
- 工作目录: {get_workdir()}
- 平台: {platform.system()}
"""

class SystemPromptBuilder:
    def __init__(self, tools: list = None):
        self.tools = tools or []
        self._static_cache: str | None = None

    def build(self) -> str:
        sections = [_build_core()]
        # 添加自我进化的学习上下文
        learnings = self._build_learnings()
        if learnings:
            sections.append(learnings)
        # 添加项目感知上下文
        proj_ctx = self._build_project_context()
        if proj_ctx:
            sections.append(proj_ctx)
        sections.append(_build_tool_listing(self.tools))
        sections.append(_build_dynamic_context())
        return "\n\n".join(sections)

    def _build_learnings(self) -> str:
        """注入学习上下文（过去会话的教训 + 成功经验）"""
        parts = []
        try:
            from src.agent.learner import get_learner, get_experience_learner
            learner = get_learner()
            ctx = learner.build_learning_context()
            if ctx:
                parts.append(ctx)
            exp = get_experience_learner()
            # Try to find relevant past episodes based on workspace name
            exp_ctx = exp.build_experience_context(str(get_workdir()))
            if exp_ctx:
                parts.append(exp_ctx)
        except Exception:
            pass
        return "\n".join(parts)

    def _build_project_context(self) -> str:
        """注入项目感知上下文（代码库结构概览）"""
        try:
            from src.tools.project_tools import project_context
            ctx = project_context()
            if ctx:
                return ctx
        except Exception:
            pass
        return ""

    def build_static(self) -> str:
        if self._static_cache:
            return self._static_cache
        sections = [_build_core(), _build_tool_listing(self.tools)]
        self._static_cache = "\n\n".join(sections)
        return self._static_cache

    def build_dynamic(self) -> str:
        return _build_dynamic_context()

    def clear_cache(self):
        self._static_cache = None


# ========== 上下文压缩器（借鉴 s06） ==========
OUTPUT_DIR = get_workdir() / ".task_outputs"
TRANSCRIPTS_DIR = get_workdir() / ".transcripts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

COMPACT_TOKEN_THRESHOLD = 50000
MAX_RECENT_TOOL_RESULTS = 3

def _content_to_dict(content) -> dict | list:
    """将 Anthropic ContentBlock 列表转换为 JSON 可序列化的 dict"""
    if isinstance(content, list):
        return [_content_to_dict(block) for block in content]
    if hasattr(content, 'type'):
        if content.type == 'text':
            return {"type": "text", "text": content.text}
        if content.type == 'thinking':
            return {"type": "thinking", "thinking": content.thinking, "signature": getattr(content, 'signature', '')}
        if content.type == 'tool_use':
            return {"type": "tool_use", "id": content.id, "name": content.name, "input": content.input}
        if content.type == 'tool_result':
            return {"type": "tool_result", "tool_use_id": content.tool_use_id, "content": content.content}
        return {"type": content.type}
    return content

def micro_compact(messages: list) -> list:
    """微型压缩：保留最近3个工具结果"""
    result = []
    tool_result_count = 0
    for msg in messages:
        msg_dict = msg if isinstance(msg, dict) else {"role": msg.role, "content": msg.content}
        if msg_dict.get("role") == "user" and isinstance(msg_dict.get("content"), list):
            new_content = []
            for block in msg_dict["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if tool_result_count < MAX_RECENT_TOOL_RESULTS:
                        new_content.append(block)
                        tool_result_count += 1
                    else:
                        new_content.append({
                            "type": "tool_result",
                            "tool_use_id": block.get("tool_use_id", "unknown"),
                            "content": f"[{len(block.get('content', ''))} chars tool output]"
                        })
                else:
                    new_content.append(block)
            msg_dict["content"] = new_content
        result.append(msg_dict)
    return result

def auto_compact(messages: list) -> list:
    """自动压缩检查"""
    # 先转换消息中的 ContentBlock 为 dict
    serializable = []
    for msg in messages:
        if isinstance(msg, dict) and "content" in msg:
            msg = dict(msg)
            msg["content"] = _content_to_dict(msg["content"])
        serializable.append(msg)
    size = len(json.dumps(serializable))
    if size > COMPACT_TOKEN_THRESHOLD:
        return micro_compact(messages)
    return messages


# ========== 错误恢复（借鉴 s11） ==========
MAX_RECOVERY_ATTEMPTS = 3
BACKOFF_BASE_DELAY = 1.0
CONTINUATION_MESSAGE = "Output limit reached. Please continue directly."

def backoff_delay(attempt: int) -> float:
    import random
    delay = BACKOFF_BASE_DELAY * (2 ** attempt) + random.random()
    return min(delay, 30.0)


# ========== 后台任务管理器（借鉴 s13） ==========
_bg_manager = None

class BackgroundManager:
    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def run(self, command: str, label: str = "") -> str:
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {
            "id": task_id, "command": command, "label": label,
            "status": "running", "started_at": time.time(), "result": None,
        }
        asyncio.create_task(self._run_background(task_id, command))
        return task_id

    async def _run_background(self, task_id: str, command: str):
        import subprocess
        try:
            r = subprocess.run(command, shell=True, cwd=get_workdir(), capture_output=True, timeout=300)
            out = r.stdout.decode('gbk', errors='replace') + r.stderr.decode('gbk', errors='replace')
            async with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "completed"
                    self._tasks[task_id]["result"] = out.strip()
        except Exception as e:
            async with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "failed"
                    self._tasks[task_id]["error"] = str(e)

    def check(self, task_id: str) -> Optional[dict]:
        return self._tasks.get(task_id)

    def list_all(self) -> list[dict]:
        return list(self._tasks.values())

def get_background_manager() -> BackgroundManager:
    global _bg_manager
    if _bg_manager is None:
        _bg_manager = BackgroundManager()
    return _bg_manager


# ========== 主 Agent Loop ==========
async def agent_loop(
    messages: list,
    system: str,
    tools: list = None,
    max_turns: int = 100,
    on_tool_call: Callable[[str, dict, str], None] = None,
    on_llm_response: Callable[[int, int], None] = None,
    session_id: str = None,
) -> str:
    """
    统一 agent loop - 整合所有增强功能
    """
    if tools is None:
        tools = ALL_TOOLS

    # 新会话：加载相关记忆并注入到会话上下文
    if session_id:
        primed = _get_memory_mgr().prime(session_id)
        if primed:
            formatted = _get_memory_mgr().format_memories(primed, max_items=10)
            memory_context = f"【跨会话记忆】以下是从之前会话中保留的重要信息，请在本次对话中参考：\n{formatted}"
            messages = [{"role": "user", "content": memory_context}] + messages

    # 自动上下文压缩
    messages = auto_compact(messages)

    client = create_client()

    try:
        for turn in range(max_turns):
            try:
                _llm_start = _metrics.record_llm_call_start()
                resp = await client.messages.create(
                    model=MODEL,
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

                if resp.stop_reason != "tool_use":
                    return "".join(b.text for b in resp.content if hasattr(b, "text"))

                # 处理工具调用
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        tool_id = block.id

                        if tool_name == "task":
                            output = await run_subagent(
                                tool_input.get("prompt", ""),
                                agent_type=tool_input.get("agent_type", "Explore"),
                            )
                            _metrics.record_tool_success(tool_name)
                        else:
                            try:
                                handler = TOOL_HANDLERS.get(tool_name, lambda **kw: f"Unknown: {tool_name}")
                                if not isinstance(tool_input, dict):
                                    output = f"Error: Invalid tool input format (expected dict, got {type(tool_input).__name__})"
                                    _metrics.record_tool_failure(tool_name, output)
                                else:
                                    output = str(handler(**tool_input))[:50000]
                                    if output.startswith("Error:"):
                                        _metrics.record_tool_failure(tool_name, output)
                                        # 自我进化：记录失败
                                        from src.agent.learner import get_learner
                                        learner = get_learner()
                                        learner.on_tool_failure(tool_name, tool_input, output, session_id)
                                    else:
                                        _metrics.record_tool_success(tool_name)
                                        # 自我进化：记录成功模式
                                        from src.agent.learner import get_learner
                                        learner = get_learner()
                                        learner.on_tool_success(tool_name, tool_input, output)
                            except Exception as tool_err:
                                output = f"Error: {tool_err}"
                                _metrics.record_tool_failure(tool_name, str(tool_err))
                                # 自我进化：记录异常
                                from src.agent.learner import get_learner
                                learner = get_learner()
                                learner.on_tool_failure(tool_name, tool_input, output, session_id)

                        if on_tool_call:
                            on_tool_call(tool_name, tool_input, output)

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": output
                        })

                messages.append({"role": "user", "content": tool_results})

                # 每轮自动压缩
                messages = auto_compact(messages)

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
    on_tool_call: Callable[[str, dict, str], None] = None,
    session_id: str = None,
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

    if session_id:
        primed = _get_memory_mgr().prime(session_id)
        if primed:
            formatted = _get_memory_mgr().format_memories(primed, max_items=10)
            memory_context = f"【跨会话记忆】以下是从之前会话中保留的重要信息，请在本次对话中参考：\n{formatted}"
            messages = [{"role": "user", "content": memory_context}] + messages

    messages = auto_compact(messages)
    client = create_client()

    try:
        for turn in range(max_turns):
            try:
                _llm_start = _metrics.record_llm_call_start()

                # Use streaming API
                stream = await client.messages.create(
                    model=MODEL,
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=LLM_MAX_TOKENS,
                    temperature=LLM_TEMPERATURE,
                    stream=True,
                )

                # Accumulate streaming response
                text_blocks: list[str] = []
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
                        if current_block_type == "tool_use" and current_tool_id:
                            try:
                                parsed_input = json.loads(current_tool_input) if current_tool_input else {}
                            except json.JSONDecodeError:
                                parsed_input = {}
                            tool_blocks.append({
                                "id": current_tool_id,
                                "name": current_tool_name,
                                "input": parsed_input,
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
                assistant_content = []
                for tb in tool_blocks:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tb["id"],
                        "name": tb["name"],
                        "input": tb["input"],
                    })
                if full_text:
                    assistant_content.append({"type": "text", "text": full_text})
                messages.append({"role": "assistant", "content": assistant_content})

                if stop_reason != "tool_use":
                    yield ("done", full_text)
                    return

                # Process tool calls
                tool_results = []
                for tb in tool_blocks:
                    tool_name = tb["name"]
                    tool_input = tb["input"]
                    tool_id = tb["id"]

                    if tool_name == "task":
                        output = await run_subagent(
                            tool_input.get("prompt", ""),
                            agent_type=tool_input.get("agent_type", "Explore"),
                        )
                        _metrics.record_tool_success(tool_name)
                    else:
                        try:
                            handler = TOOL_HANDLERS.get(tool_name, lambda **kw: f"Unknown: {tool_name}")
                            if not isinstance(tool_input, dict):
                                output = f"Error: Invalid tool input format"
                                _metrics.record_tool_failure(tool_name, output)
                            else:
                                output = str(handler(**tool_input))[:50000]
                                if output.startswith("Error:"):
                                    _metrics.record_tool_failure(tool_name, output)
                                    from src.agent.learner import get_learner
                                    learner = get_learner()
                                    learner.on_tool_failure(tool_name, tool_input, output, session_id)
                                else:
                                    _metrics.record_tool_success(tool_name)
                                    from src.agent.learner import get_learner
                                    learner = get_learner()
                                    learner.on_tool_success(tool_name, tool_input, output)
                        except Exception as tool_err:
                            output = f"Error: {tool_err}"
                            _metrics.record_tool_failure(tool_name, str(tool_err))

                    yield ("tool_result", tool_name, tool_input, output)
                    if on_tool_call:
                        on_tool_call(tool_name, tool_input, output)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": output,
                    })

                messages.append({"role": "user", "content": tool_results})
                messages = auto_compact(messages)

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
        await client.close()


# ========== 导出 ==========
__all__ = [
    "agent_loop", "agent_loop_stream", "run_subagent",
    "RunSession", "get_run_session", "cleanup_run_session",
    "BASE_TOOLS", "TODO_TOOLS", "TASK_TOOLS", "SUBAGENT_TOOLS", "ALL_TOOLS", "TOOLS",
    "TOOL_HANDLERS",
    "TodoManager", "TaskManager", "BackgroundManager",
    "SystemPromptBuilder", "DYNAMIC_BOUNDARY",
    "auto_compact", "micro_compact", "backoff_delay",
    "get_background_manager",
    "create_client", "get_workdir", "MODEL",
]
