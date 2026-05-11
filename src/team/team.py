"""
Team System - 借鉴 s15/s16/s17 agent_teams

整合的团队协作系统：
- MessageBus: JSONL inbox 异步通信
- RequestStore: 持久化协议请求（shutdown/plan_approval）
- TeammateManager: 团队成员生命周期管理
- AutonomousTeammate: 空闲轮询 + 自动认领任务 + identity re-injection
"""

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(override=False)

from src.infra.config import WORKSPACE_DIR
from src.infra.llm_config import MODEL, API_KEY, BASE_URL, create_client, create_sync_client

WORKDIR = Path(WORKSPACE_DIR)

def _run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return f"Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True, timeout=120)
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

def _run_read(path: str, limit: int = None) -> str:
    try:
        fp = (WORKDIR / path).resolve()
        if not str(fp).startswith(str(WORKDIR)):
            return f"Error: Path escapes workspace"
        content = fp.read_text(encoding="utf-8")
        if limit:
            lines = content.splitlines()
            if len(lines) > limit:
                content = "\n".join(lines[:limit]) + f"\n... ({len(lines) - limit} more lines)"
        return content[:50000]
    except Exception as e:
        return f"Error: {e}"

def _run_write(path: str, content: str) -> str:
    try:
        fp = (WORKDIR / path).resolve()
        if not str(fp).startswith(str(WORKDIR)):
            return f"Error: Path escapes workspace"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"

def _run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = (WORKDIR / path).resolve()
        if not str(fp).startswith(str(WORKDIR)):
            return f"Error: Path escapes workspace"
        content = fp.read_text(encoding="utf-8")
        if old_text not in content:
            return f"Error: Text not found"
        content = content.replace(old_text, new_text, 1)
        fp.write_text(content, encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"
TEAM_DIR = WORKDIR / ".team"
INBOX_DIR = TEAM_DIR / "inbox"
TASKS_DIR = WORKDIR / ".tasks"
REQUESTS_DIR = TEAM_DIR / "requests"

INBOX_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR.mkdir(parents=True, exist_ok=True)
REQUESTS_DIR.mkdir(parents=True, exist_ok=True)

# Valid message types
VALID_MSG_TYPES = {
    "message", "broadcast",
    "shutdown_request", "shutdown_response",
    "plan_approval", "plan_approval_response",
}

# ========== MessageBus ==========
class MessageBus:
    """
    JSONL inbox per teammate.
    Send/receive messages, broadcast to all teammates.
    """

    def __init__(self, inbox_dir: Path = None):
        self.dir = inbox_dir or INBOX_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def _inbox_path(self, name: str) -> Path:
        safe = name.replace("/", "_").replace("\\", "_")
        return self.dir / f"{safe}.jsonl"

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: dict = None,
    ) -> str:
        if msg_type not in VALID_MSG_TYPES:
            return f"Error: Invalid type '{msg_type}'. Valid: {VALID_MSG_TYPES}"

        msg = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            msg.update(extra)

        inbox_path = self._inbox_path(to)
        with open(inbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        return f"Sent {msg_type} to {to}"

    def read_inbox(self, name: str) -> list[dict]:
        inbox_path = self._inbox_path(name)
        if not inbox_path.exists():
            return []

        messages = []
        try:
            content = inbox_path.read_text(encoding="utf-8").strip()
            for line in content.splitlines():
                if line:
                    messages.append(json.loads(line))
        except (json.JSONDecodeError, OSError):
            pass

        # Drain: write empty content
        try:
            inbox_path.write_text("", encoding="utf-8")
        except OSError:
            pass

        return messages

    def broadcast(self, sender: str, content: str, teammates: list[str]) -> str:
        count = 0
        for name in teammates:
            if name != sender:
                self.send(sender, name, content, "broadcast")
                count += 1
        return f"Broadcast to {count} teammates"


# 全局 MessageBus
BUS = MessageBus()


# ========== RequestStore ==========
class RequestStore:
    """
    持久化协议请求记录（shutdown/plan_approval）。
    每个 request_id 一个 JSON 文件。
    """

    def __init__(self, requests_dir: Path = None):
        self.dir = requests_dir or REQUESTS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, request_id: str) -> Path:
        return self.dir / f"{request_id}.json"

    def create(self, record: dict) -> dict:
        request_id = record["request_id"]
        with self._lock:
            self._path(request_id).write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
        return record

    def get(self, request_id: str) -> Optional[dict]:
        path = self._path(request_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def update(self, request_id: str, **changes) -> Optional[dict]:
        with self._lock:
            record = self.get(request_id)
            if not record:
                return None
            record.update(changes)
            record["updated_at"] = time.time()
            self._path(request_id).write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
        return record


REQUEST_STORE = RequestStore()


# ========== Task Board（借鉴 s17） ==========
_claim_lock = threading.Lock()


def scan_unclaimed_tasks(role: str = None) -> list[dict]:
    """扫描未认领的任务"""
    TASKS_DIR.mkdir(exist_ok=True)
    unclaimed = []
    for f in sorted(TASKS_DIR.glob("task_*.json")):
        try:
            task = json.loads(f.read_text(encoding="utf-8"))
            if (
                task.get("status") == "pending"
                and not task.get("owner")
                and not task.get("blockedBy")
            ):
                if role and task.get("claim_role") and task["claim_role"] != role:
                    continue
                unclaimed.append(task)
        except (json.JSONDecodeError, OSError):
            pass
    return unclaimed


def claim_task(task_id: str, owner: str, role: str = None, source: str = "manual") -> str:
    """原子性认领任务"""
    with _claim_lock:
        path = TASKS_DIR / f"task_{task_id}.json"
        if not path.exists():
            return f"Error: Task {task_id} not found"

        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return f"Error: Invalid task file"

        if task.get("owner") or task.get("status") != "pending":
            return f"Error: Task {task_id} not claimable"

        task["owner"] = owner
        task["status"] = "in_progress"
        task["claimed_at"] = time.time()
        task["claim_source"] = source

        path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")

    # 记录 claim 事件
    CLAIM_EVENTS_PATH = TASKS_DIR / "claim_events.jsonl"
    try:
        with open(CLAIM_EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "task.claimed",
                "task_id": task_id,
                "owner": owner,
                "role": role,
                "source": source,
                "ts": time.time(),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass

    return f"Claimed task #{task_id} for {owner} via {source}"


# ========== Identity Block（借鉴 s17） ==========
def make_identity_block(name: str, role: str, team_name: str) -> dict:
    return {
        "role": "user",
        "content": f"<identity>You are '{name}', role: {role}, team: {team_name}. Continue your work.</identity>",
    }


def ensure_identity_context(messages: list, name: str, role: str, team_name: str):
    """确保 identity block 在上下文顶部（压缩后重新注入）"""
    if messages and "<identity>" in str(messages[0].get("content", "")):
        return
    messages.insert(0, make_identity_block(name, role, team_name))
    messages.insert(1, {"role": "assistant", "content": f"I am {name}. Continuing."})


# ========== TeammateManager ==========
class TeammateManager:
    """
    团队成员管理器 - 持久化命名成员 + 后台线程生命周期
    """

    def __init__(self, team_dir: Path = None):
        self.dir = team_dir or TEAM_DIR
        self.dir.mkdir(exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.config = self._load_config()
        self.threads: dict[str, threading.Thread] = {}

    def _load_config(self) -> dict:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"team_name": "default", "members": []}

    def _save_config(self):
        self.config_path.write_text(
            json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _find_member(self, name: str) -> Optional[dict]:
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    def _set_status(self, name: str, status: str):
        member = self._find_member(name)
        if member:
            member["status"] = status
            self._save_config()

    def spawn(self, name: str, role: str, prompt: str, autonomous: bool = True) -> str:
        """Spawn 一个团队成员"""
        member = self._find_member(name)
        if member:
            if member["status"] not in ("idle", "shutdown"):
                return f"Error: '{name}' is currently {member['status']}"
            member["status"] = "working"
            member["role"] = role
        else:
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)
        self._save_config()

        thread = threading.Thread(
            target=self._loop,
            args=(name, role, prompt, autonomous),
            daemon=True,
        )
        self.threads[name] = thread
        thread.start()
        return f"Spawned '{name}' (role: {role}, autonomous={autonomous})"

    def _loop(self, name: str, role: str, prompt: str, autonomous: bool):
        """成员主循环：WORK → IDLE → (inbox/tasks/timeout) → SHUTDOWN"""
        team_name = self.config["team_name"]
        sys_prompt = (
            f"You are '{name}', role: {role}, team: {team_name}, at {WORKDIR}. "
            f"Use idle tool when you have no more work."
        )

        messages = [{"role": "user", "content": prompt}]
        tools = self._teammate_tools()
        client = create_sync_client()

        while True:
            # === WORK PHASE ===
            for _ in range(50):
                inbox = BUS.read_inbox(name)
                for msg in inbox:
                    if msg.get("type") == "shutdown_request":
                        self._set_status(name, "shutdown")
                        return
                    messages.append({"role": "user", "content": json.dumps(msg)})

                try:
                    resp = client.messages.create(
                        model=MODEL,
                        system=sys_prompt,
                        messages=messages,
                        tools=tools,
                        max_tokens=8000,
                    )
                except Exception:
                    self._set_status(name, "idle")
                    return

                from src.agent.engine import _content_to_dict
                messages.append({"role": "assistant", "content": _content_to_dict(resp.content)})

                if resp.stop_reason != "tool_use":
                    break

                results = []
                idle_requested = False
                for block in resp.content:
                    if block.type == "tool_use":
                        if block.name == "idle":
                            idle_requested = True
                            output = "Entering idle phase. Will poll for new tasks."
                        else:
                            output = self._exec(name, block.name, block.input)

                        results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(output)[:50000],
                        })

                messages.append({"role": "user", "content": results})

                if idle_requested:
                    break

            if not autonomous:
                # 非自主成员完成工作后进入 shutdown
                self._set_status(name, "shutdown")
                return

            # === IDLE PHASE：轮询 inbox 和任务板 ===
            self._set_status(name, "idle")
            polls = 12  # 60s / 5s
            for _ in range(polls):
                time.sleep(5)

                # 检查 inbox
                inbox = BUS.read_inbox(name)
                if inbox:
                    ensure_identity_context(messages, name, role, team_name)
                    for msg in inbox:
                        if msg.get("type") == "shutdown_request":
                            self._set_status(name, "shutdown")
                            return
                        messages.append({"role": "user", "content": json.dumps(msg)})
                    self._set_status(name, "working")
                    break

                # 扫描未认领任务
                unclaimed = scan_unclaimed_tasks(role)
                if unclaimed:
                    task = unclaimed[0]
                    result = claim_task(str(task["id"]), name, role=role, source="auto")
                    if not result.startswith("Error:"):
                        task_prompt = (
                            f"<auto-claimed>Task #{task['id']}: {task.get('subject', task.get('description', ''))}\n"
                            f"{task.get('description', '')}</auto-claimed>"
                        )
                        ensure_identity_context(messages, name, role, team_name)
                        messages.append({"role": "user", "content": task_prompt})
                        messages.append({"role": "assistant", "content": f"{result}. Working on it."})
                        self._set_status(name, "working")
                        break
            else:
                # 超时 → shutdown
                self._set_status(name, "shutdown")
                return

    def _exec(self, sender: str, tool_name: str, args: dict) -> str:
        """执行工具（供团队成员使用）"""
        if tool_name == "bash":
            return _run_bash(args["command"])
        if tool_name == "read_file":
            return _run_read(args["path"], args.get("limit"))
        if tool_name == "write_file":
            return _run_write(args["path"], args["content"])
        if tool_name == "edit_file":
            return _run_edit(args["path"], args["old_text"], args["new_text"])
        if tool_name == "send_message":
            return BUS.send(sender, args["to"], args["content"], args.get("msg_type", "message"))
        if tool_name == "read_inbox":
            return json.dumps(BUS.read_inbox(sender), ensure_ascii=False)
        if tool_name == "shutdown_response":
            req_id = args["request_id"]
            approve = args["approve"]
            REQUEST_STORE.update(
                req_id,
                status="approved" if approve else "rejected",
                resolved_by=sender,
                resolved_at=time.time(),
                response={"approve": approve, "reason": args.get("reason", "")},
            )
            BUS.send(
                sender, "lead", args.get("reason", ""),
                "shutdown_response", {"request_id": req_id, "approve": approve},
            )
            return f"Shutdown {'approved' if approve else 'rejected'}"
        if tool_name == "plan_approval":
            plan_text = args.get("plan", "")
            req_id = str(uuid.uuid4())[:8]
            REQUEST_STORE.create({
                "request_id": req_id,
                "kind": "plan_approval",
                "from": sender,
                "to": "lead",
                "status": "pending",
                "plan": plan_text,
                "created_at": time.time(),
                "updated_at": time.time(),
            })
            BUS.send(
                sender, "lead", plan_text, "plan_approval",
                {"request_id": req_id, "plan": plan_text},
            )
            return f"Plan submitted (request_id={req_id}). Waiting for lead approval."
        if tool_name == "claim_task":
            return claim_task(
                args["task_id"],
                sender,
                role=self._find_member(sender).get("role") if self._find_member(sender) else None,
                source="manual",
            )
        return f"Unknown tool: {tool_name}"

    def _teammate_tools(self) -> list:
        """团队成员可用的工具列表"""
        return [
            {"name": "bash", "description": "Run a shell command.",
             "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
            {"name": "read_file", "description": "Read file contents.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
            {"name": "write_file", "description": "Write content to file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Replace exact text in file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
            {"name": "send_message", "description": "Send message to a teammate.",
             "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)}}, "required": ["to", "content"]}},
            {"name": "read_inbox", "description": "Read and drain your inbox.",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "shutdown_response", "description": "Respond to a shutdown request.",
             "input_schema": {"type": "object", "properties": {"request_id": {"type": "string"}, "approve": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["request_id", "approve"]}},
            {"name": "plan_approval", "description": "Submit a plan for lead approval.",
             "input_schema": {"type": "object", "properties": {"plan": {"type": "string"}}, "required": ["plan"]}},
            {"name": "idle", "description": "Signal no more work. Enters idle polling phase.",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "claim_task", "description": "Claim a task from the task board by ID.",
             "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
        ]

    def list_all(self) -> str:
        """列出所有成员状态"""
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    def member_names(self) -> list[str]:
        return [m["name"] for m in self.config["members"]]


# ========== Lead 协议处理器 ==========
def handle_shutdown_request(teammate: str) -> str:
    """Lead 发送 shutdown 请求"""
    req_id = str(uuid.uuid4())[:8]
    REQUEST_STORE.create({
        "request_id": req_id,
        "kind": "shutdown",
        "from": "lead",
        "to": teammate,
        "status": "pending",
        "created_at": time.time(),
        "updated_at": time.time(),
    })
    BUS.send(
        "lead", teammate, "Please shut down gracefully.",
        "shutdown_request", {"request_id": req_id},
    )
    return f"Shutdown request {req_id} sent to '{teammate}' (status: pending)"


def handle_plan_review(request_id: str, approve: bool, feedback: str = "") -> str:
    """Lead 审批团队成员的计划"""
    req = REQUEST_STORE.get(request_id)
    if not req:
        return f"Error: Unknown plan request_id '{request_id}'"

    REQUEST_STORE.update(
        request_id,
        status="approved" if approve else "rejected",
        reviewed_by="lead",
        resolved_at=time.time(),
        feedback=feedback,
    )

    BUS.send(
        "lead", req["from"], feedback, "plan_approval_response",
        {"request_id": request_id, "approve": approve, "feedback": feedback},
    )

    return f"Plan {'approved' if approve else 'rejected'} for '{req['from']}'"


def check_request_status(request_id: str) -> dict:
    """查询请求状态"""
    return REQUEST_STORE.get(request_id) or {"error": "not found"}


# ========== 团队工具（供 Lead 使用） ==========
TEAM_TOOLS = [
    {"name": "spawn_teammate", "description": "Spawn a persistent teammate that runs in its own thread.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}, "prompt": {"type": "string"}, "autonomous": {"type": "boolean"}}, "required": ["name", "role", "prompt"]}},
    {"name": "list_teammates", "description": "List all teammates with name, role, status.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_message", "description": "Send a message to a teammate's inbox.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)}}, "required": ["to", "content"]}},
    {"name": "read_inbox", "description": "Read and drain the lead's inbox.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "broadcast", "description": "Send a message to all teammates.",
     "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    {"name": "shutdown_request", "description": "Request a teammate to shut down gracefully. Returns a request_id for tracking.",
     "input_schema": {"type": "object", "properties": {"teammate": {"type": "string"}}, "required": ["teammate"]}},
    {"name": "shutdown_response", "description": "Check the status of a shutdown request by request_id.",
     "input_schema": {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": ["request_id"]}},
    {"name": "plan_approval", "description": "Approve or reject a teammate's plan. Provide request_id + approve + optional feedback.",
     "input_schema": {"type": "object", "properties": {"request_id": {"type": "string"}, "approve": {"type": "boolean"}, "feedback": {"type": "string"}}, "required": ["request_id", "approve"]}},
    {"name": "claim_task", "description": "Claim a task from the task board by ID.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
]


# ========== 全局单例 ==========
_team_manager: Optional[TeammateManager] = None


def get_team_manager() -> TeammateManager:
    global _team_manager
    if _team_manager is None:
        _team_manager = TeammateManager()
    return _team_manager


__all__ = [
    "MessageBus", "RequestStore", "TeammateManager",
    "BUS", "REQUEST_STORE",
    "get_team_manager", "scan_unclaimed_tasks", "claim_task",
    "handle_shutdown_request", "handle_plan_review", "check_request_status",
    "make_identity_block", "ensure_identity_context",
    "TEAM_TOOLS", "VALID_MSG_TYPES",
]