"""
Team System - Real Multi-Agent Collaboration

Each teammate is a genuine LLM-powered agent with:
- Role-specific system prompts that define distinct behavior
- Independent agent loops with proper tool calling
- JSONL inbox-based async communication
- Auto task claiming from the shared task board
- Task result reporting protocol
- Graceful shutdown protocol

Architecture:
    Lead Agent spawns → Planner / Coder / Tester / Reviewer / Designer
    Each teammate runs in its own daemon thread with its own LLM client.
    Communication happens via MessageBus (JSONL inbox files).
    Tasks are coordinated via the shared TaskManager (.tasks/ directory).
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

from src.infra.config import WORKSPACE_DIR, LLM_MAX_TOKENS, LLM_TEMPERATURE
from src.infra.llm_config import MODEL, API_KEY, BASE_URL, create_client, create_sync_client

WORKDIR = Path(WORKSPACE_DIR)

# ========== Role-Specific System Prompts ==========

ROLE_SYSTEM_PROMPTS = {
    "planner": """You are the **Planner** — the strategic architect of the team.

**Your Job:**
1. Analyze the user's requirements deeply. Identify what's explicitly asked AND what's implicitly needed.
2. Break the work into clear, ordered tasks. Each task must be independently verifiable.
3. Define acceptance criteria for each task — what does "done" look like?
4. Identify risks, dependencies, and edge cases UPFRONT.
5. Assign each task to the right role (Coder, Tester, Designer, etc.).

**Output Format:**
For each task, output:
- Task title + description
- Owner role
- Acceptance criteria (checkable!)
- Dependencies on other tasks
- Risk level (low/medium/high)

**Tool Guidelines:**
- Use task_create for each task you define
- Use search_codebase / project_context to understand the existing code before planning
- Use send_message to coordinate with other agents
- When your plan is complete, use send_message to notify the Lead agent

**Important:** You are not writing code. You are planning. Delegate implementation to the Coder.""",

    "coder": """You are the **Coder** — the hands-on implementer.

**Your Job:**
1. Take tasks assigned to you (especially from the Planner) and implement them.
2. Write clean, working code. Prefer small, incremental changes.
3. After each file change, verify the syntax is correct.
4. Handle edge cases and error conditions.
5. Report what you changed and why.

**Tool Guidelines:**
- Use read_file first to understand existing code before editing
- Use edit_file with line numbers (start_line/end_line) for precise edits — ALWAYS read the file first to get exact line numbers
- Use write_file for new files
- Use bash to run syntax checks and basic tests after changes
- Use send_message to report completion or ask for clarification
- Use task_update to mark tasks as completed

**Code Quality:**
- Follow existing code patterns in the project
- Keep functions small and focused
- Add minimal, necessary comments only where logic is non-obvious
- Handle errors gracefully

**Important:** You are writing real code that must work. Test your changes before reporting done.""",

    "tester": """You are the **Tester** — the quality guardian.

**Your Job:**
1. Verify that implemented code meets the acceptance criteria.
2. Run tests, check edge cases, and validate behavior.
3. When tests fail, provide clear reproduction steps.
4. Write test cases for untested functionality.
5. Report verification results with evidence.

**Verification Checklist:**
- Does the code run without errors?
- Does it meet ALL acceptance criteria?
- Are edge cases handled?
- Is there test coverage?
- Are there any regressions?

**Tool Guidelines:**
- Use bash to run test commands and check outputs
- Use read_file to review code changes against requirements
- Use search_codebase to find related tests
- Use write_file to add missing test cases
- Use send_message to report verification results to the team
- Use task_update to mark tasks as verified

**Important:** Your verification results are the final word on quality. Be thorough.""",

    "reviewer": """You are the **Reviewer** — the risk and quality auditor.

**Your Job:**
1. Review code changes for bugs, security issues, and design problems.
2. Assess the risk level of each change.
3. Check that the implementation matches the plan.
4. Verify that all acceptance criteria are actually met.
5. Produce a review report with findings and recommendations.

**Review Checklist:**
- Code correctness and logic errors
- Security vulnerabilities (injection, path traversal, etc.)
- Performance concerns
- Missing error handling
- Breaking changes to existing APIs
- Test adequacy

**Tool Guidelines:**
- Use read_file to review changed files
- Use bash to run linters or static analysis
- Use search_codebase to check for affected code
- Use send_message to flag issues to the Coder
- Use task_update to mark review status

**Important:** Your review is the last line of defense. If something is wrong, say so clearly and specifically.""",

    "designer": """You are the **Designer** — the user experience specialist.

**Your Job:**
1. Review UI/UX implementation for quality and consistency.
2. Check visual hierarchy, spacing, color usage, and responsive behavior.
3. Ensure the interface follows best practices for accessibility.
4. Verify interaction continuity — buttons work, states are clear, feedback is immediate.
5. Suggest improvements for polish and user delight.

**Review Checklist:**
- Information density is appropriate
- Visual hierarchy is clear
- Interactive elements are discoverable
- States (loading, empty, error, success) are handled
- Layout works at different sizes
- Color contrast meets accessibility standards

**Tool Guidelines:**
- Use read_file to inspect UI code
- Use edit_file for visual polish changes
- Use send_message to report UI issues and suggestions

**Important:** Beauty matters. But usability matters more. Prioritize clear, functional interfaces.""",

    "lead": """You are the **Lead Agent** — the team orchestrator.

**Your Job:**
1. Understand the user's goal and assemble the right team.
2. Delegate work by spawning teammates and sending them clear instructions.
3. Monitor progress by reading inbox messages and checking task status.
4. Make final decisions when the team disagrees.
5. Synthesize results into a clear final response to the user.

**Orchestration Protocol:**
1. Analyze the request → what roles are needed?
2. Spawn teammates with spawn_teammate, giving them specific, actionable prompts
3. Send detailed task assignments via send_message
4. Wait for completion reports via read_inbox
5. If stuck, create tasks on the task board for auto-claim
6. Synthesize and report final results

**Tool Guidelines:**
- spawn_teammate: Create agents with clear role + specific prompt
- send_message: Assign work to specific teammates
- broadcast: Notify the whole team
- read_inbox: Check for responses
- task_create / task_update: Manage the shared task board
- All file and bash tools for direct work when needed

**Important:** You are responsible for the final outcome. If a teammate isn't delivering, step in directly.""",
}


def _build_role_prompt(role: str, name: str, team_name: str) -> str:
    """Build a role-specific system prompt for a teammate."""
    role_lower = role.lower().strip()

    # Check for exact match first
    for key, prompt in ROLE_SYSTEM_PROMPTS.items():
        if key in role_lower:
            header = f"You are **{name}**, the {role} of team '{team_name}'.\n"
            footer = f"\n\n=== System ===\nWorking directory: {WORKDIR}\nTeam: {team_name}\nYour name: {name}\nYour role: {role}\nUse the 'idle' tool when you have no more work to do."
            return header + prompt + footer

    # Generic fallback for unknown roles
    return (
        f"You are **{name}**, role '{role}' in team '{team_name}'.\n\n"
        f"Work directory: {WORKDIR}.\n"
        f"You have access to file operations, bash commands, team messaging, and task management tools.\n"
        f"Complete your assigned work, report results via send_message to the Lead, "
        f"then use the 'idle' tool when finished.\n"
        f"If you see unclaimed tasks matching your role on the task board, claim and complete them."
    )


# ========== Local tool implementations (workspace-scoped) ==========

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
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def _run_edit(path: str, old_text: str = "", new_text: str = "", start_line: int = None, end_line: int = None) -> str:
    """Edit file - supports line-based (preferred) and legacy string-based replacement."""
    import difflib
    try:
        fp = (WORKDIR / path).resolve()
        if not str(fp).startswith(str(WORKDIR)):
            return f"Error: Path escapes workspace"
        if not fp.exists():
            return f"Error: File not found: {path}"

        content = fp.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        if start_line is not None and end_line is not None:
            if start_line < 1 or end_line > len(lines) or start_line > end_line:
                return f"Error: Invalid line range {start_line}-{end_line} (file has {len(lines)} lines)"
            old_slice = "".join(lines[start_line-1:end_line])
            new_lines_list = new_text.splitlines(keepends=True)
            if new_lines_list and not new_lines_list[-1].endswith("\n"):
                new_lines_list[-1] += "\n"
            new_content = "".join(lines[:start_line-1] + new_lines_list + lines[end_line:])
            loc = f"lines {start_line}-{end_line}"
            new_slice = "".join(new_lines_list)
        elif old_text:
            if old_text not in content:
                return f"Error: Text not found in file. Try using start_line/end_line for line-based edits (read the file first to get line numbers)."
            old_slice = old_text
            new_slice = new_text
            new_content = content.replace(old_text, new_text, 1)
            loc = "matched text"
        else:
            return "Error: Provide either (old_text, new_text) or (start_line, end_line, new_text)"

        fp.write_text(new_content, encoding="utf-8")

        diff_lines = list(difflib.unified_diff(
            old_slice.splitlines(keepends=True),
            new_slice.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
        ))
        diff_text = "\n".join(diff_lines[:30])
        added = len(new_slice) - len(old_slice)
        change = f"+{added}" if added >= 0 else str(added)
        return f"Edited {path} ({loc}, {change} chars)\n```diff\n{diff_text}\n```"
    except Exception as e:
        return f"Error: {e}"

def _run_list_dir(path: str = ".") -> str:
    try:
        fp = (WORKDIR / path).resolve()
        if not str(fp).startswith(str(WORKDIR)):
            return f"Error: Path escapes workspace"
        items = list(fp.iterdir())
        lines = []
        for item in sorted(items, key=lambda x: (not x.is_dir(), x.name)):
            suffix = "/" if item.is_dir() else ""
            size = ""
            if item.is_file():
                try:
                    size = f" ({item.stat().st_size} bytes)"
                except OSError:
                    pass
            name = item.name
            if name.startswith("."):
                continue
            lines.append(f"{name}{suffix}{size}")
        return "\n".join(lines) if lines else "(empty directory)"
    except Exception as e:
        return f"Error: {e}"


# ========== Directories ==========

TEAM_DIR = WORKDIR / ".team"
INBOX_DIR = TEAM_DIR / "inbox"
TASKS_DIR = WORKDIR / ".tasks"
REQUESTS_DIR = TEAM_DIR / "requests"

INBOX_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR.mkdir(parents=True, exist_ok=True)
REQUESTS_DIR.mkdir(parents=True, exist_ok=True)

VALID_MSG_TYPES = {
    "message", "broadcast",
    "shutdown_request", "shutdown_response",
    "plan_approval", "plan_approval_response",
    "task_assignment", "task_complete", "task_review",
    "progress_update", "help_request",
}


# ========== MessageBus ==========

class MessageBus:
    """JSONL inbox per teammate for async communication."""

    def __init__(self, inbox_dir: Path = None):
        self.dir = inbox_dir or INBOX_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def _inbox_path(self, name: str) -> Path:
        safe = name.replace("/", "_").replace("\\", "_")
        return self.dir / f"{safe}.jsonl"

    def send(self, sender: str, to: str, content: str, msg_type: str = "message", extra: dict = None) -> str:
        if msg_type not in VALID_MSG_TYPES:
            return f"Error: Invalid type '{msg_type}'. Valid: {sorted(VALID_MSG_TYPES)}"
        msg = {"type": msg_type, "from": sender, "content": content, "timestamp": time.time()}
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


BUS = MessageBus()


# ========== RequestStore ==========

class RequestStore:
    """Persistent protocol request records (shutdown/plan_approval)."""

    def __init__(self, requests_dir: Path = None):
        self.dir = requests_dir or REQUESTS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, request_id: str) -> Path:
        return self.dir / f"{request_id}.json"

    def create(self, record: dict) -> dict:
        request_id = record["request_id"]
        with self._lock:
            self._path(request_id).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
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
            self._path(request_id).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return record


REQUEST_STORE = RequestStore()


# ========== Task Board ==========

_claim_lock = threading.Lock()

def scan_unclaimed_tasks(role: str = None) -> list[dict]:
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

    CLAIM_EVENTS_PATH = TASKS_DIR / "claim_events.jsonl"
    try:
        with open(CLAIM_EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "task.claimed",
                "task_id": task_id, "owner": owner, "role": role,
                "source": source, "ts": time.time(),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return f"Claimed task #{task_id} for {owner} via {source}"


# ========== Identity Block ==========

def make_identity_block(name: str, role: str, team_name: str) -> dict:
    return {
        "role": "user",
        "content": f"<identity>You are '{name}', role: {role}, team: {team_name}. Continue your work.</identity>",
    }

def ensure_identity_context(messages: list, name: str, role: str, team_name: str):
    if messages and "<identity>" in str(messages[0].get("content", "")):
        return
    messages.insert(0, make_identity_block(name, role, team_name))
    messages.insert(1, {"role": "assistant", "content": f"I am {name}. Continuing."})


# ========== TeammateManager ==========

class TeammateManager:
    """Manages persistent named teammates with real LLM-powered agent loops."""

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
        self.config_path.write_text(json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8")

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
        """Spawn a teammate as a real LLM-powered agent thread."""
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
            target=self._agent_loop,
            args=(name, role, prompt, autonomous),
            daemon=True,
        )
        self.threads[name] = thread
        thread.start()

        BUS.send("system", name,
            f"You have been spawned as '{name}' with role '{role}'. "
            f"Your task: {prompt}",
            "task_assignment")
        return f"Spawned '{name}' (role: {role}, autonomous={autonomous})"

    def _agent_loop(self, name: str, role: str, prompt: str, autonomous: bool):
        """Real LLM-powered agent loop for each teammate."""
        team_name = self.config["team_name"]
        system = _build_role_prompt(role, name, team_name)
        messages = [{"role": "user", "content": prompt}]
        tools = self._teammate_tools()
        client = create_sync_client()

        try:
            while True:
                # === WORK PHASE: process messages and tasks ===
                for _ in range(30):
                    # Check inbox for new messages
                    inbox = BUS.read_inbox(name)
                    for msg in inbox:
                        if msg.get("type") == "shutdown_request":
                            self._handle_shutdown(name, client, messages, msg)
                            return
                        # Format inbox message for LLM context
                        context = (
                            f"[Message from {msg.get('from', 'unknown')} "
                            f"({msg.get('type', 'message')})]: {msg.get('content', '')}"
                        )
                        messages.append({"role": "user", "content": context})

                    try:
                        resp = client.messages.create(
                            model=MODEL,
                            system=system,
                            messages=messages,
                            tools=tools,
                            max_tokens=LLM_MAX_TOKENS,
                            temperature=LLM_TEMPERATURE,
                        )
                    except Exception as e:
                        self._set_status(name, "idle")
                        BUS.send(name, "lead",
                            f"Error during LLM call: {e}. Going idle.",
                            "progress_update")
                        return

                    from src.agent.engine import _content_to_dict
                    messages.append({"role": "assistant", "content": _content_to_dict(resp.content)})

                    if resp.stop_reason != "tool_use":
                        # Agent produced a text response — it's done thinking
                        break

                    # Process tool calls
                    results = []
                    idle_requested = False
                    for block in resp.content:
                        if block.type == "tool_use":
                            if block.name == "idle":
                                idle_requested = True
                                results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": "Entering idle. Will poll for new tasks."
                                })
                            else:
                                output = self._exec_tool(name, block.name, block.input)
                                results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": str(output)[:50000],
                                })

                    messages.append({"role": "user", "content": results})
                    if idle_requested:
                        break

                if not autonomous:
                    self._set_status(name, "shutdown")
                    BUS.send(name, "lead",
                        f"Task completed. Shutting down (non-autonomous mode).",
                        "task_complete")
                    return

                # === IDLE PHASE: poll inbox and task board ===
                self._set_status(name, "idle")
                for _ in range(12):  # 60s total
                    time.sleep(5)
                    inbox = BUS.read_inbox(name)
                    if inbox:
                        ensure_identity_context(messages, name, role, team_name)
                        for msg in inbox:
                            if msg.get("type") == "shutdown_request":
                                self._handle_shutdown(name, client, messages, msg)
                                return
                            context = (
                                f"[Message from {msg.get('from', 'unknown')} "
                                f"({msg.get('type')})]: {msg.get('content', '')}"
                            )
                            messages.append({"role": "user", "content": context})
                        self._set_status(name, "working")
                        break

                    # Auto-claim matching tasks
                    unclaimed = scan_unclaimed_tasks(role)
                    if unclaimed:
                        task = unclaimed[0]
                        result = claim_task(str(task["id"]), name, role=role, source="auto")
                        if not result.startswith("Error:"):
                            task_prompt = (
                                f"<auto-claimed task>\n"
                                f"Task #{task['id']}: {task.get('subject', '')}\n"
                                f"Description: {task.get('description', 'No description')}\n"
                                f"</auto-claimed task>\n\n"
                                f"Work on this task now. When done, use task_update to mark it completed "
                                f"and send_message to report results to 'lead'."
                            )
                            ensure_identity_context(messages, name, role, team_name)
                            messages.append({"role": "user", "content": task_prompt})
                            self._set_status(name, "working")
                            break
                else:
                    self._set_status(name, "shutdown")
                    BUS.send(name, "lead",
                        f"Idle timeout reached. Shutting down gracefully.",
                        "progress_update")
                    return
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _handle_shutdown(self, name: str, client, messages: list, msg: dict):
        """Handle a shutdown request gracefully."""
        self._set_status(name, "shutdown")
        req_id = msg.get("request_id", "unknown")
        REQUEST_STORE.update(req_id, status="approved", resolved_by=name, resolved_at=time.time())
        BUS.send(name, "lead", "Shutting down.", "shutdown_response", {"request_id": req_id, "approve": True})

    def _exec_tool(self, sender: str, tool_name: str, args: dict) -> str:
        """Execute a tool on behalf of a teammate."""
        try:
            if tool_name == "bash":
                return _run_bash(args.get("command", ""))
            elif tool_name == "read_file":
                return _run_read(args.get("path", ""), args.get("limit"))
            elif tool_name == "write_file":
                return _run_write(args.get("path", ""), args.get("content", ""))
            elif tool_name == "edit_file":
                return _run_edit(
                    args.get("path", ""),
                    args.get("old_text", ""),
                    args.get("new_text", ""),
                    args.get("start_line"),
                    args.get("end_line"),
                )
            elif tool_name == "list_directory":
                return _run_list_dir(args.get("path", "."))
            elif tool_name == "project_context":
                from src.tools.project_tools import project_context as _pc
                return _pc()
            elif tool_name == "search_codebase":
                from src.tools.project_tools import search_codebase as _sc
                return _sc(args.get("query", ""), args.get("search_type", "symbol"))
            elif tool_name == "send_message":
                return BUS.send(sender, args["to"], args["content"], args.get("msg_type", "message"))
            elif tool_name == "read_inbox":
                msgs = BUS.read_inbox(sender)
                return json.dumps(msgs, ensure_ascii=False) if msgs else "(inbox empty)"
            elif tool_name == "broadcast":
                tm = get_team_manager()
                return BUS.broadcast(sender, args.get("content", ""), tm.member_names())
            elif tool_name == "shutdown_response":
                req_id = args["request_id"]
                approve = args["approve"]
                REQUEST_STORE.update(req_id, status="approved" if approve else "rejected",
                    resolved_by=sender, resolved_at=time.time(),
                    response={"approve": approve, "reason": args.get("reason", "")})
                BUS.send(sender, "lead", args.get("reason", ""),
                    "shutdown_response", {"request_id": req_id, "approve": approve})
                return f"Shutdown {'approved' if approve else 'rejected'}"
            elif tool_name == "plan_approval":
                plan_text = args.get("plan", "")
                req_id = str(uuid.uuid4())[:8]
                REQUEST_STORE.create({
                    "request_id": req_id, "kind": "plan_approval",
                    "from": sender, "to": "lead", "status": "pending",
                    "plan": plan_text, "created_at": time.time(), "updated_at": time.time(),
                })
                BUS.send(sender, "lead", plan_text, "plan_approval",
                    {"request_id": req_id, "plan": plan_text})
                return f"Plan submitted (request_id={req_id}). Waiting for lead approval."
            elif tool_name == "claim_task":
                return claim_task(args["task_id"], sender,
                    role=self._find_member(sender).get("role") if self._find_member(sender) else None,
                    source="manual")
            elif tool_name == "task_create":
                from src.agent.engine import get_task_manager
                tm = get_task_manager()
                task = tm.create(
                    args.get("subject", ""),
                    args.get("description", ""),
                    args.get("blocked_by", []),
                )
                return f"Created task {task['id']}: {task['subject']}"
            elif tool_name == "task_update":
                from src.agent.engine import get_task_manager
                tm = get_task_manager()
                tm.update_status(args.get("task_id", ""), args.get("status", ""))
                return f"Updated task {args.get('task_id')} to {args.get('status')}"
            elif tool_name == "task_list":
                from src.agent.engine import get_task_manager
                tm = get_task_manager()
                tasks = tm.list_all()
                if args.get("status"):
                    tasks = [t for t in tasks if t["status"] == args["status"]]
                return json.dumps(tasks, ensure_ascii=False) if tasks else "No tasks"
            elif tool_name == "TodoWrite":
                from src.agent.engine import get_todo_manager
                tdm = get_todo_manager()
                results = []
                for item in args.get("items", []):
                    content = item.get("content", "")
                    status = item.get("status", "pending")
                    if status == "completed":
                        for t in tdm.items:
                            if t.content == content:
                                tdm.update(t.id, "completed")
                                results.append(f"Completed: {content}")
                                break
                    else:
                        tid = tdm.add(content)
                        results.append(f"Added: {content} (id={tid})")
                return "\n".join(results)
            elif tool_name == "TodoList":
                from src.agent.engine import get_todo_manager
                tdm = get_todo_manager()
                if not tdm.items:
                    return "No todos"
                lines = []
                for t in tdm.items:
                    icon = "✓" if t.status == "completed" else "○"
                    lines.append(f"[{icon}] {t.content} ({t.status})")
                return "\n".join(lines)
            elif tool_name == "add_memory":
                from src.tools.memory_tools import add_memory
                return add_memory(
                    args.get("content", ""),
                    args.get("category", "project"),
                    args.get("importance", 1),
                    args.get("tags"),
                )
            elif tool_name == "recall_memories":
                from src.tools.memory_tools import recall_memories
                return recall_memories(
                    args.get("query", ""),
                    args.get("category"),
                    args.get("limit", 10),
                )
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            return f"Error executing {tool_name}: {e}"

    def _teammate_tools(self) -> list:
        """Tools available to every teammate — full toolset matching Lead agent."""
        return [
            {"name": "bash", "description": "Run a shell command in the workspace.",
             "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
            {"name": "read_file", "description": "Read file contents. Use before editing to get exact line numbers.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
            {"name": "write_file", "description": "Create or overwrite a file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Edit a file. PREFERRED: use start_line/end_line with line numbers (read the file first!). Legacy: use old_text/new_text.",
             "input_schema": {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "start_line": {"type": "integer", "description": "1-indexed start line (preferred)"},
                 "end_line": {"type": "integer", "description": "1-indexed end line, inclusive (preferred)"},
                 "old_text": {"type": "string", "description": "Legacy: text to find and replace"},
                 "new_text": {"type": "string", "description": "Replacement text"},
             }, "required": ["path", "new_text"]}},
            {"name": "list_directory", "description": "List directory contents.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
            {"name": "project_context", "description": "Get project structure overview (entry points, modules, stats).",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "search_codebase", "description": "Search the codebase for symbols, files, or text patterns.",
             "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "search_type": {"type": "string", "enum": ["symbol", "file", "text"]}}, "required": ["query", "search_type"]}},
            {"name": "send_message", "description": "Send a message to a specific teammate or 'lead'.",
             "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string"}}, "required": ["to", "content"]}},
            {"name": "broadcast", "description": "Send a message to ALL teammates.",
             "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
            {"name": "read_inbox", "description": "Read and clear your inbox messages.",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "task_create", "description": "Create a new task on the shared board.",
             "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}, "blocked_by": {"type": "array", "items": {"type": "string"}}}, "required": ["subject"]}},
            {"name": "task_update", "description": "Update task status (pending/in_progress/completed).",
             "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "status": {"type": "string"}}, "required": ["task_id", "status"]}},
            {"name": "task_list", "description": "List tasks on the shared board.",
             "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}}},
            {"name": "claim_task", "description": "Claim an unclaimed task by ID.",
             "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
            {"name": "TodoWrite", "description": "Manage personal todo items.",
             "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string"}}}}}}},
            {"name": "TodoList", "description": "List personal todos.",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "shutdown_response", "description": "Respond to a shutdown request.",
             "input_schema": {"type": "object", "properties": {"request_id": {"type": "string"}, "approve": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["request_id", "approve"]}},
            {"name": "plan_approval", "description": "Submit a plan for Lead approval.",
             "input_schema": {"type": "object", "properties": {"plan": {"type": "string"}}, "required": ["plan"]}},
            {"name": "idle", "description": "Signal no more work. Enter idle polling mode.",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "add_memory", "description": "Store a persistent memory for future sessions.",
             "input_schema": {"type": "object", "properties": {"content": {"type": "string"}, "category": {"type": "string"}, "importance": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["content", "category"]}},
            {"name": "recall_memories", "description": "Search persisted memories.",
             "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "category": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
        ]

    def list_all(self) -> str:
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    def member_names(self) -> list[str]:
        return [m["name"] for m in self.config["members"]]


# ========== Lead Protocol Handlers ==========

def handle_shutdown_request(teammate: str) -> str:
    req_id = str(uuid.uuid4())[:8]
    REQUEST_STORE.create({
        "request_id": req_id, "kind": "shutdown",
        "from": "lead", "to": teammate, "status": "pending",
        "created_at": time.time(), "updated_at": time.time(),
    })
    BUS.send("lead", teammate, "Please shut down gracefully.",
        "shutdown_request", {"request_id": req_id})
    return f"Shutdown request {req_id} sent to '{teammate}'"


def handle_plan_review(request_id: str, approve: bool, feedback: str = "") -> str:
    req = REQUEST_STORE.get(request_id)
    if not req:
        return f"Error: Unknown plan request_id '{request_id}'"
    REQUEST_STORE.update(request_id,
        status="approved" if approve else "rejected",
        reviewed_by="lead", resolved_at=time.time(), feedback=feedback)
    BUS.send("lead", req["from"], feedback, "plan_approval_response",
        {"request_id": request_id, "approve": approve, "feedback": feedback})
    return f"Plan {'approved' if approve else 'rejected'} for '{req['from']}'"


def check_request_status(request_id: str) -> dict:
    return REQUEST_STORE.get(request_id) or {"error": "not found"}


# ========== Team Tools (for Lead Agent) ==========

TEAM_TOOLS = [
    {"name": "spawn_teammate", "description": "Spawn a persistent AI teammate with a specific role and task. Choose from: planner, coder, tester, reviewer, designer.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Unique name for the teammate"},
         "role": {"type": "string", "description": "Role: planner, coder, tester, reviewer, designer, or lead"},
         "prompt": {"type": "string", "description": "Specific task instructions for this teammate"},
         "autonomous": {"type": "boolean", "description": "If true, teammate polls for more tasks when done"},
     }, "required": ["name", "role", "prompt"]}},
    {"name": "list_teammates", "description": "List all teammates with name, role, and status.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_message", "description": "Send a message to a teammate's inbox.",
     "input_schema": {"type": "object", "properties": {
         "to": {"type": "string", "description": "Teammate name or 'lead'"},
         "content": {"type": "string"},
         "msg_type": {"type": "string", "enum": sorted(VALID_MSG_TYPES)},
     }, "required": ["to", "content"]}},
    {"name": "read_inbox", "description": "Read and drain the lead's inbox.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "broadcast", "description": "Send a message to all teammates.",
     "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    {"name": "shutdown_request", "description": "Request a teammate to shut down gracefully.",
     "input_schema": {"type": "object", "properties": {"teammate": {"type": "string"}}, "required": ["teammate"]}},
    {"name": "shutdown_response", "description": "Check shutdown request status.",
     "input_schema": {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": ["request_id"]}},
    {"name": "plan_approval", "description": "Approve or reject a teammate's plan.",
     "input_schema": {"type": "object", "properties": {
         "request_id": {"type": "string"}, "approve": {"type": "boolean"}, "feedback": {"type": "string"},
     }, "required": ["request_id", "approve"]}},
    {"name": "claim_task", "description": "Claim a task from the board.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
]


# ========== Global Singleton ==========

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
    "TEAM_TOOLS", "VALID_MSG_TYPES", "ROLE_SYSTEM_PROMPTS",
]
