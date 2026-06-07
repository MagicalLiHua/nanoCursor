"""ToolPolicyRuntime: enforce tool policy at call time."""

from __future__ import annotations

import time
import uuid
import shlex
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from src.runtime.run_budget import RunBudget

READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "read_file_range",
        "read_function",
        "read_class",
        "list_directory",
        "search_codebase",
        "project_context",
        "git_status",
        "git_diff",
        "task_list",
        "recall_memories",
    }
)
SAFE_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "task_create",
        "task_update",
        "add_memory",
        "spawn_agent",
    }
)
RISKY_WRITE_TOOLS = frozenset(
    {
        "delete_file",
        "move_file",
        "rollback_file",
        "restore_snapshot",
        "apply_patch",
    }
)
SHELL_TOOLS = frozenset({"bash", "run_bash", "run_tests"})
HIGH_RISK_LEVELS = frozenset({"risky_write", "shell_risky", "external_risky", "mcp_write"})
DEFAULT_APPROVAL_LEVELS = frozenset({"risky_write", "shell_risky", "external_risky", "mcp_write"})

_SENSITIVE_FILE_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".gitignore",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "go.mod",
        "go.sum",
        "cargo.toml",
        "cargo.lock",
        "pom.xml",
        "build.gradle",
        "settings.gradle",
        "makefile",
    }
)
_SENSITIVE_PATH_SEGMENTS = frozenset(
    {
        ".github",
        ".gitlab",
        ".circleci",
        ".husky",
        ".ssh",
        ".aws",
        ".kube",
        "secrets",
        "credentials",
    }
)
_SENSITIVE_PATH_TOKENS = (
    "secret",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "token",
    "password",
)
_LARGE_WRITE_BYTES = 200 * 1024
_LARGE_EDIT_TEXT_BYTES = 12_000
_LARGE_EDIT_LINES = 200

_SHELL_SAFE_PREFIXES = (
    ("ls",),
    ("dir",),
    ("echo",),
    ("pwd",),
    ("cat",),
    ("type",),
    ("head",),
    ("tail",),
    ("grep",),
    ("rg",),
    ("find",),
    ("git", "status"),
    ("git", "diff"),
    ("pytest",),
    ("ruff",),
    ("mypy",),
    ("eslint",),
    ("tsc", "--noEmit"),
    ("node", "--check"),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("python", "-m", "unittest"),
    ("python3", "-m", "unittest"),
    ("python", "-m", "py_compile"),
    ("python3", "-m", "py_compile"),
    ("python", "--version"),
    ("python3", "--version"),
    ("npm", "test"),
    ("npm", "run", "test"),
    ("npm", "run", "check"),
    ("npm", "run", "lint"),
    ("npm", "run", "typecheck"),
    ("npm", "run", "build"),
)

_SHELL_RISKY_TOKENS = frozenset(
    {
        "rm",
        "del",
        "rmdir",
        "mv",
        "move",
        "cp",
        "copy",
        "chmod",
        "chown",
        "sudo",
        "git",
        "pip",
        "pip3",
        "uv",
        "poetry",
        "npm",
        "pnpm",
        "yarn",
        "curl",
        "wget",
        "ssh",
        "scp",
        "docker",
        "kubectl",
    }
)
_SHELL_RISKY_PATTERNS = (
    "rm -rf",
    "git reset",
    "git clean",
    "git checkout",
    "git switch",
    "git commit",
    "git push",
    "pip install",
    "npm install",
    "pnpm install",
    "yarn install",
    "curl ",
    "wget ",
    "http://",
    "https://",
    ">",
    ">>",
    "| sh",
    "| bash",
)


def _tokens_start_with(tokens: list[str], prefix: tuple[str, ...]) -> bool:
    if len(tokens) < len(prefix):
        return False
    return tuple(token.lower() for token in tokens[: len(prefix)]) == prefix


def _is_safe_python_script(tokens: list[str]) -> bool:
    if len(tokens) < 2:
        return False
    if tokens[0].lower() not in {"python", "python3"}:
        return False
    script = tokens[1]
    if script.startswith("-") or not script.endswith(".py"):
        return False
    if script.startswith(("/", "~")) or ".." in script.split("/"):
        return False
    for token in tokens[2:]:
        lowered = token.lower()
        if any(marker in lowered for marker in [";", "&&", "||", "|", ">", "<", "`", "$("]):
            return False
        if token.startswith(("/", "~")) or ".." in token.split("/"):
            return False
        if token.startswith("-") and not re.match(r"^--?[a-zA-Z0-9][a-zA-Z0-9_-]*(=.*)?$", token):
            return False
    return True


def _strip_safe_cd_prefix(tokens: list[str]) -> list[str] | None:
    if len(tokens) >= 5 and tokens[0].lower() == "cd" and tokens[2] == "&&":
        target = tokens[1]
        if target.startswith(("/", "./", "../", "~")):
            return tokens[3:]
    return None


def _strip_echo_fallback(tokens: list[str]) -> list[str]:
    if "||" not in tokens:
        return tokens
    index = tokens.index("||")
    if index + 1 < len(tokens) and tokens[index + 1].lower() == "echo":
        return tokens[:index]
    return tokens


def _strip_timeout_prefix(tokens: list[str]) -> list[str]:
    if len(tokens) >= 3 and tokens[0].lower() == "timeout" and tokens[1].isdigit():
        return tokens[2:]
    return tokens


def classify_shell_command(command: str) -> str:
    """Classify shell command permission: shell_safe or shell_risky."""
    text = (command or "").strip()
    if not text:
        return "shell_risky"
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return "shell_risky"
    tokens = _strip_timeout_prefix(_strip_echo_fallback([token for token in tokens if token != "2>&1"]))
    if not tokens:
        return "shell_risky"
    lowered = " ".join(tokens).lower()
    if any(pattern in lowered for pattern in _SHELL_RISKY_PATTERNS):
        return "shell_risky"

    safe_cd_tail = _strip_safe_cd_prefix(tokens)
    safe_cd_tail = _strip_timeout_prefix(safe_cd_tail) if safe_cd_tail else None
    if safe_cd_tail and (
        _is_safe_python_script(safe_cd_tail)
        or any(_tokens_start_with(safe_cd_tail, prefix) for prefix in _SHELL_SAFE_PREFIXES)
    ):
        return "shell_safe"

    # Compound commands are hard to prove safe without a shell parser.
    if any(token in {";", "&&", "||", "|", "&"} for token in tokens):
        return "shell_risky"

    head = tokens[0].lower()
    if head in {"find"} and any(token.lower() == "-delete" for token in tokens):
        return "shell_risky"
    if head in _SHELL_RISKY_TOKENS:
        if any(_tokens_start_with(tokens, prefix) for prefix in _SHELL_SAFE_PREFIXES):
            return "shell_safe"
        if _is_safe_python_script(tokens):
            return "shell_safe"
        return "shell_risky"
    if any(_tokens_start_with(tokens, prefix) for prefix in _SHELL_SAFE_PREFIXES):
        return "shell_safe"
    if _is_safe_python_script(tokens):
        return "shell_safe"
    return "shell_risky"


def classify_tool_permission(tool_name: str, tool_input: dict[str, Any] | None = None) -> str:
    """Return nanoCursor's coarse permission level for a tool call."""
    name = str(tool_name or "").strip()
    payload = tool_input if isinstance(tool_input, dict) else {}
    if name == "run_tests":
        return "shell_safe"
    if name in SHELL_TOOLS:
        return classify_shell_command(str(payload.get("command", "")))
    if name in READ_ONLY_TOOLS:
        return "read_only"
    if name in RISKY_WRITE_TOOLS:
        return "risky_write"
    if name in SAFE_WRITE_TOOLS:
        if name in {"write_file", "edit_file"} and _is_sensitive_write_target(payload):
            return "risky_write"
        if name == "write_file" and _is_large_write_payload(payload):
            return "risky_write"
        if name == "edit_file":
            old_text = str(payload.get("old_text", "") or "")
            new_text = str(payload.get("new_text", "") or "")
            start_line = payload.get("start_line")
            end_line = payload.get("end_line")
            try:
                line_span = int(end_line) - int(start_line) + 1 if start_line and end_line else 0
            except (TypeError, ValueError):
                line_span = 0
            if max(len(old_text), len(new_text)) > _LARGE_EDIT_TEXT_BYTES or line_span > _LARGE_EDIT_LINES:
                return "risky_write"
        return "safe_write"
    if name.startswith("mcp_") or name == "mcp_call":
        return _classify_mcp_tool_permission(name, payload)
    return "external_risky"


def _file_tool_path(payload: dict[str, Any]) -> str:
    return str(
        payload.get("path")
        or payload.get("file_path")
        or payload.get("filename")
        or payload.get("target")
        or ""
    ).strip()


def _is_sensitive_write_target(payload: dict[str, Any]) -> bool:
    path = _file_tool_path(payload).replace("\\", "/").strip()
    if not path:
        return False
    lowered = path.lower().strip("/")
    parts = [part for part in lowered.split("/") if part and part not in {".", ".."}]
    basename = parts[-1] if parts else lowered
    if basename in _SENSITIVE_FILE_NAMES:
        return True
    if any(part in _SENSITIVE_PATH_SEGMENTS for part in parts):
        return True
    return any(token in lowered for token in _SENSITIVE_PATH_TOKENS)


def _is_large_write_payload(payload: dict[str, Any]) -> bool:
    content = str(payload.get("content") or payload.get("new_text") or "")
    return len(content.encode("utf-8", errors="ignore")) > _LARGE_WRITE_BYTES


def _classify_mcp_tool_permission(tool_name: str, payload: dict[str, Any]) -> str:
    """Classify MCP tool calls for runtime tool governance."""
    explicit = str(
        payload.get("permission_level")
        or payload.get("permission")
        or payload.get("access")
        or payload.get("mode")
        or ""
    ).strip().lower()
    if explicit in {"mcp_read", "read", "readonly", "read_only"}:
        return "mcp_read"
    if explicit in {"mcp_write", "write", "mutation", "mutate"}:
        return "mcp_write"

    name = str(payload.get("tool_name") or payload.get("tool") or tool_name or "").strip().lower().replace("-", "_")
    if any(token in name for token in _MCP_WRITE_TOKENS):
        return "mcp_write"
    if any(name.startswith(prefix) or f"_{prefix}" in name for prefix in _MCP_READ_PREFIXES):
        return "mcp_read"
    return "external_risky"


_MCP_READ_PREFIXES = (
    "list",
    "get",
    "read",
    "search",
    "find",
    "query",
    "fetch",
    "inspect",
    "describe",
    "resolve",
    "lookup",
)

_MCP_WRITE_TOKENS = (
    "create",
    "update",
    "delete",
    "remove",
    "write",
    "edit",
    "mutate",
    "submit",
    "approve",
    "merge",
    "commit",
    "push",
    "post",
    "upload",
    "install",
)


@dataclass
class ToolPolicyDecision:
    tool: str = ""
    allowed: bool = True
    requires_approval: bool = False
    reason: str = ""
    budget_exceeded: list[str] = field(default_factory=list)
    decision_id: str = ""
    risk_level: str = "medium"
    permission_level: str = "external_risky"
    status: Literal["pending", "approved", "rejected", "auto_allowed", "blocked"] = "auto_allowed"
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.decision_id:
            self.decision_id = f"approval_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = time.time()
        # Derive status from allowed / requires_approval flags
        if not self.allowed:
            self.status = "blocked"
        elif self.requires_approval:
            self.status = "pending"
        # else: keep "auto_allowed"

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "tool": self.tool,
            "permission_level": self.permission_level,
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "status": self.status,
            "budget_exceeded": self.budget_exceeded,
            "created_at": self.created_at,
        }


class ToolPolicyRuntime:
    """Enforce tool policy and budget at runtime."""

    # B6: Thresholds for runtime adaptation
    ESCALATION_FAILURE_THRESHOLD = 3
    BONUS_BUDGET_SUCCESS_STREAK = 5
    BONUS_BUDGET_FACTOR = 0.2  # Add 20% more budget

    def __init__(self, policy: dict[str, Any] | None = None, budget: RunBudget | None = None):
        p = policy or {}
        self.allowed_tools: list[str] = list(p.get("allowed_tools", []))
        self.denied_tools: list[str] = list(p.get("denied_tools", []))
        self.approval_required: list[str] = list(p.get("approval_required", []))
        self.approval_required_levels: set[str] = set(
            p.get("approval_required_levels", DEFAULT_APPROVAL_LEVELS)
        )
        self.risk_level: str = p.get("risk_level", "medium")
        self.budget = budget or RunBudget()
        self.decisions: list[dict] = []
        # B6: Runtime adaptation state
        self.consecutive_failures: int = 0
        self.success_streak: int = 0
        self._escalated: bool = False
        self._budget_boosted: bool = False
        self._write_approval_added: bool = False

    def check(self, tool_name: str, tool_input: dict[str, Any] | None = None) -> ToolPolicyDecision:
        permission_level = classify_tool_permission(tool_name, tool_input)
        payload = tool_input if isinstance(tool_input, dict) else {}

        # 1. Denied
        if tool_name in self.denied_tools:
            d = ToolPolicyDecision(
                tool=tool_name, allowed=False,
                reason=f"{tool_name} 在当前策略中被禁止。",
                risk_level=self.risk_level,
                permission_level=permission_level,
            )
            self.decisions.append(d.to_dict())
            return d

        # 2. Not in allowed (if allowed list is non-empty)
        if self.allowed_tools and tool_name not in self.allowed_tools:
            d = ToolPolicyDecision(
                tool=tool_name, allowed=False,
                reason=f"{tool_name} 不在允许列表中。",
                risk_level=self.risk_level,
                permission_level=permission_level,
            )
            self.decisions.append(d.to_dict())
            return d

        # 3. Budget exceeded
        exceeded = self.budget.exceeded_for(tool_name)
        if exceeded:
            d = ToolPolicyDecision(
                tool=tool_name, allowed=False,
                reason=f"预算超限: {exceeded}", budget_exceeded=exceeded,
                risk_level=self.risk_level,
                permission_level=permission_level,
            )
            self.decisions.append(d.to_dict())
            return d

        # 3.5. Invalid shell calls should be repaired by the agent, not approved by the user.
        if tool_name in SHELL_TOOLS and tool_name != "run_tests" and not str(payload.get("command") or "").strip():
            d = ToolPolicyDecision(
                tool=tool_name,
                allowed=False,
                status="blocked",
                reason=f"{tool_name} 缺少 command 参数，无法执行或审批。",
                risk_level=self.risk_level,
                permission_level=permission_level,
            )
            self.decisions.append(d.to_dict())
            return d

        # 4. Requires approval
        if tool_name in self.approval_required or permission_level in self.approval_required_levels:
            d = ToolPolicyDecision(
                tool=tool_name,
                allowed=True,
                requires_approval=True,
                status="pending",
                reason=(
                    f"{tool_name} 权限等级为 {permission_level}，"
                    f"在当前策略 ({self.risk_level}) 下需要审批。"
                ),
                risk_level=self.risk_level,
                permission_level=permission_level,
            )
            self.decisions.append(d.to_dict())
            return d

        # 5. Allowed
        d = ToolPolicyDecision(
            tool=tool_name, allowed=True, status="auto_allowed",
            reason=f"{permission_level} 权限自动放行。", risk_level=self.risk_level,
            permission_level=permission_level,
        )
        self.decisions.append(d.to_dict())
        return d

    def record(self, tool_name: str, ok: bool) -> dict[str, Any] | None:
        """Record a tool call result and adapt policy if needed.

        Returns an adaptation event dict if policy was changed, else None.
        """
        self.budget.record_tool(tool_name)

        # B6: Track failure/success streaks
        if ok:
            self.consecutive_failures = 0
            self.success_streak += 1
        else:
            self.success_streak = 0
            self.consecutive_failures += 1

        # B6: Escalate after consecutive failures
        if self.consecutive_failures >= self.ESCALATION_FAILURE_THRESHOLD and not self._escalated:
            self._escalated = True
            # Add write tools to approval_required if not already there
            newly_added = []
            for write_tool in ("write_file", "edit_file"):
                if write_tool not in self.approval_required:
                    self.approval_required.append(write_tool)
                    newly_added.append(write_tool)
            self._write_approval_added = True
            if newly_added:
                return {
                    "type": "policy_escalated",
                    "reason": f"连续 {self.consecutive_failures} 次失败，写操作已升级为需要审批",
                    "tools_added_to_approval": newly_added,
                }

        # B6: Boost budget after sustained success
        if (self.success_streak >= self.BONUS_BUDGET_SUCCESS_STREAK
                and not self._budget_boosted
                and not self._escalated):
            self._budget_boosted = True
            bonus_calls = max(1, int(self.budget.max_tool_calls * self.BONUS_BUDGET_FACTOR))
            bonus_writes = max(1, int(self.budget.max_file_writes * self.BONUS_BUDGET_FACTOR))
            self.budget.max_tool_calls += bonus_calls
            self.budget.max_file_writes += bonus_writes
            return {
                "type": "policy_relaxed",
                "reason": f"连续 {self.success_streak} 次成功，预算已扩展",
                "bonus_tool_calls": bonus_calls,
                "bonus_file_writes": bonus_writes,
            }
        return None

    def to_dict(self) -> dict:
        return {
            "policy": {
                "allowed_tools": self.allowed_tools,
                "denied_tools": self.denied_tools,
                "approval_required": self.approval_required,
                "approval_required_levels": sorted(self.approval_required_levels),
                "risk_level": self.risk_level,
            },
            "budget": self.budget.to_dict(),
            "decisions": self.decisions[-20:],
            "adaptation": {
                "consecutive_failures": self.consecutive_failures,
                "success_streak": self.success_streak,
                "escalated": self._escalated,
                "budget_boosted": self._budget_boosted,
            },
        }

    def violations(self) -> list[dict]:
        return [d for d in self.decisions if not d.get("allowed", True)]
