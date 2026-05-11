"""
Permission Manager - 借鉴 s07_permission_system.py

权限管道：deny_rules → mode_check → allow_rules → ask_user

模式：default, plan, auto
"""

import os
import re
from pathlib import Path
from typing import Literal
from src.infra.config import WORKSPACE_DIR
WORKDIR = Path(WORKSPACE_DIR)


TRUST_MARKER = ".claude/.claude_trusted"

# 危险 bash 命令模式
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+[/]",
    r"sudo\s+",
    r"shutdown",
    r"reboot",
    r">\s*/dev/",
    r"&\s*;\s*rm",
    r"eval\s+",
    r"base64\s+-d\s+",
]


class BashSecurityValidator:
    """
    Bash 安全验证器 - 阻止危险命令
    """

    _pattern_cache = [(re.compile(p), p) for p in DANGEROUS_PATTERNS]

    @classmethod
    def is_dangerous(cls, command: str) -> bool:
        """检查命令是否危险"""
        for pattern, _ in cls._pattern_cache:
            if pattern.search(command):
                return True
        return False

    @classmethod
    def validate(cls, command: str) -> tuple[bool, str]:
        """
        验证命令，返回 (是否安全, 原因)
        """
        if cls.is_dangerous(command):
            return False, "Command matches dangerous pattern"
        return True, ""


class PermissionManager:
    """
    权限管理器
    """

    DEFAULT_MODE: Literal["default", "plan", "auto"] = "default"
    WRITE_TOOLS = {"write_file", "edit_file", "bash", "delete_file"}
    READ_ONLY_TOOLS = {"read_file", "list_directory"}

    def __init__(self):
        self.mode = self.DEFAULT_MODE
        self._deny_rules: list[str] = []
        self._allow_rules: list[str] = []
        self._consecutive_denials = 0

    def set_mode(self, mode: Literal["default", "plan", "auto"]):
        """设置权限模式"""
        self.mode = mode

    def check(self, tool_name: str, tool_input: dict) -> tuple[str, str]:
        """
        检查工具调用是否允许。

        返回 (behavior, reason)
        - behavior: "allow" | "deny" | "ask"
        - reason: str
        """
        # 1. 先检查 deny rules
        for rule in self._deny_rules:
            if self._matches_rule(tool_name, tool_input, rule):
                return "deny", f"Denied by rule: {rule}"

        # 2. 检查模式
        if self.mode == "plan":
            # plan 模式下只读工具允许，写工具需要 ask
            if tool_name in self.WRITE_TOOLS:
                return "ask", "Plan mode requires approval for write operations"

        # 3. 检查 allow rules
        for rule in self._allow_rules:
            if self._matches_rule(tool_name, tool_input, rule):
                return "allow", f"Allowed by rule: {rule}"

        # 4. 默认允许
        return "allow", "Default allow"

    def ask_user(self, tool_name: str, tool_input: dict) -> bool:
        """
        请求用户授权（交互式）。
        在 API 模式下可以返回 False。
        """
        print(f"\n[Permission] {tool_name} requires approval")
        print(f"Input: {tool_input}")
        # API 模式下默认拒绝
        if os.getenv("NANOCURSOR_API_MODE"):
            return False
        # 交互模式可以扩展
        return False

    def _matches_rule(self, tool_name: str, tool_input: dict, rule: str) -> bool:
        """检查是否匹配规则"""
        if rule.startswith("tool:"):
            return tool_name == rule[5:]
        if rule.startswith("path:"):
            path = tool_input.get("path", "")
            return rule[5:] in path
        return False

    def is_workspace_trusted(self) -> bool:
        """检查工作区是否受信任"""
        return (WORKDIR / TRUST_MARKER).exists()


# 全局单例
_permission_manager: PermissionManager | None = None


def get_permission_manager() -> PermissionManager:
    global _permission_manager
    if _permission_manager is None:
        _permission_manager = PermissionManager()
    return _permission_manager


__all__ = ["PermissionManager", "BashSecurityValidator", "get_permission_manager"]