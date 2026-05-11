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

def get_workdir() -> Path:
    """Return the current workspace directory (always reads latest value from config)."""
    return Path(_config.WORKSPACE_DIR).resolve()

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
    {"name": "edit_file", "description": "Edit file text",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "list_directory", "description": "List directory",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}},
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

# Team tools from team.py
from src.team.team import (
    get_team_manager, BUS, REQUEST_STORE,
    scan_unclaimed_tasks, claim_task,
    handle_shutdown_request as _handle_shutdown_req, handle_plan_review as _handle_plan_review, check_request_status as _check_request_status,
    make_identity_block, ensure_identity_context,
    TEAM_TOOLS as _TEAM_TOOLS,
)

# Add team tools to ALL_TOOLS
ALL_TOOLS = BASE_TOOLS + TODO_TOOLS + TASK_TOOLS + SUBAGENT_TOOLS + _TEAM_TOOLS
# Alias for backwards compatibility
TOOLS = ALL_TOOLS


# ========== 工具处理函数 ==========
import subprocess

def safe_path(p: str) -> Path:
    path = (get_workdir() / p).resolve()
    if not str(path).startswith(str(get_workdir())):
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
        except:
            out = (r.stdout or b'') + (r.stderr or b'')
            if isinstance(out, bytes):
                out = out.decode('utf-8', errors='replace')
        return out.strip()[:50000] or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except Exception as e:
        return f"Error: {e}"

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
        fp.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        content = safe_path(path).read_text(encoding="utf-8")
        if old_text not in content:
            return f"Error: Text not found"
        content = content.replace(old_text, new_text, 1)
        safe_path(path).write_text(content, encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# ========== Todo 管理器 ==========
TODO_FILE = get_workdir() / ".todos.json"

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
        if TODO_FILE.exists():
            try:
                data = json.loads(TODO_FILE.read_text(encoding="utf-8"))
                self.items = [TodoItem(**t) for t in data]
            except:
                self.items = []

    def _save(self):
        data = [{"id": t.id, "content": t.content, "status": t.status, "created_at": t.created_at} for t in self.items]
        TODO_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

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
        self.tasks_dir = (tasks_dir or TASKS_DIR)
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
            except:
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

    for turn in range(30):
        resp = await client.messages.create(
            model=MODEL, system=system, messages=messages,
            tools=BASE_TOOLS, max_tokens=8000,
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
    "edit_file": _safe_handler(["path", "old_text", "new_text"], lambda path, old_text, new_text: run_edit(path, old_text, new_text)),
    "list_directory": lambda **kw: run_bash(f'dir /b "{kw.get("path", ".")}" 2>nul'),
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
    return f"""你是一个自动编程助手，在 {get_workdir()} 工作。

【重要】你运行在 Windows 系统上！使用 Windows 命令：
- 用 `dir` 而不是 `ls`
- 用 `type` 而不是 `cat`
- 用 `del` 而不是 `rm`
- 用 `copy` 而不是 `cp`

你有以下工具：
- bash: 执行 shell 命令（参数：command）
- read_file: 读取文件（参数：path, limit 可选）
- write_file: 写文件（参数：path, content）
- edit_file: 编辑文件（参数：path, old_text, new_text）
- list_directory: 列出目录内容（参数：path）
- TodoWrite: 添加/更新 todo
- TodoList: 列出所有 todo
- task_create: 创建任务
- task_update: 更新任务状态
- task_list: 列出任务
- task: 启动子代理
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
        sections = [_build_core(), _build_tool_listing(self.tools), _build_dynamic_context()]
        return "\n\n".join(sections)

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
    session_id: str = None,
) -> str:
    """
    统一 agent loop - 整合所有增强功能
    """
    if tools is None:
        tools = ALL_TOOLS

    # 自动上下文压缩
    messages = auto_compact(messages)

    client = create_client()

    for turn in range(max_turns):
        try:
            _llm_start = _metrics.record_llm_call_start()
            resp = await client.messages.create(
                model=MODEL,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=4096,
            )
            _metrics.record_llm_call_end(_llm_start, resp.usage.input_tokens + resp.usage.output_tokens)

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
                            # guard: ensure tool_input is a dict (some APIs return JSON string)
                            if not isinstance(tool_input, dict):
                                output = f"Error: Invalid tool input format (expected dict, got {type(tool_input).__name__})"
                                _metrics.record_tool_failure(tool_name, output)
                            else:
                                output = str(handler(**tool_input))[:50000]
                                if output.startswith("Error:"):
                                    _metrics.record_tool_failure(tool_name, output)
                                else:
                                    _metrics.record_tool_success(tool_name)
                        except Exception as tool_err:
                            output = f"Error: {tool_err}"
                            _metrics.record_tool_failure(tool_name, str(tool_err))

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


# ========== 导出 ==========
__all__ = [
    "agent_loop", "run_subagent",
    "BASE_TOOLS", "TODO_TOOLS", "TASK_TOOLS", "SUBAGENT_TOOLS", "ALL_TOOLS", "TOOLS",
    "TOOL_HANDLERS",
    "TodoManager", "TaskManager", "BackgroundManager",
    "SystemPromptBuilder", "DYNAMIC_BOUNDARY",
    "auto_compact", "micro_compact", "backoff_delay",
    "get_background_manager",
    "create_client", "get_workdir", "MODEL",
]