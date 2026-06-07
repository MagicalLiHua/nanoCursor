"""
Hook Manager - 借鉴 s08_hook_system.py

在 agent loop 的关键节点（session start, pre-tool, post-tool）
执行钩子扩展点，支持命令式和结构化输出。
"""

import json
import os
from pathlib import Path
from typing import Optional
from src.infra.config import WORKSPACE_DIR
from src.runtime.command_runner import run_command
WORKDIR = Path(WORKSPACE_DIR)


TRUST_MARKER = ".claude/.claude_trusted"
HOOK_CONFIG = ".hooks.json"


class HookManager:
    """
    钩子管理器 - 在关键事件点执行扩展

    事件类型：
    - SessionStart: 会话启动时
    - PreToolUse: 工具执行前
    - PostToolUse: 工具执行后

    退出码：
    - 0 = continue（继续执行）
    - 1 = block（阻止执行）
    - 2 = inject message（注入消息）
    """

    def __init__(self, hooks_dir: Path = None):
        self.hooks_dir = hooks_dir or (WORKDIR / ".hooks")
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        self._hooks: dict[str, list[dict]] = {}
        self._load_hooks()

    def _load_hooks(self):
        """加载钩子配置"""
        config_file = WORKDIR / HOOK_CONFIG
        if config_file.exists():
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
                self._hooks = data.get("hooks", {})
            except Exception:
                self._hooks = {}

    def _is_trusted(self) -> bool:
        """检查工作区是否受信任"""
        return (WORKDIR / TRUST_MARKER).exists()

    def run_hooks(
        self,
        event: str,
        context: dict,
        env: dict | None = None
    ) -> tuple[bool, list[str], dict]:
        """
        运行指定事件的钩子。

        返回 (blocked, injected_messages, updated_context)
        """
        # SDK 模式跳过信任检查
        is_trusted = self._is_trusted() or os.getenv("CLAUDE_SDK_MODE") == "true"

        hooks_to_run = self._hooks.get(event, [])
        blocked = False
        injected_messages = []
        updated_context = context.copy()

        for hook in hooks_to_run:
            matcher = hook.get("matcher", "")

            # 按 tool name 过滤
            if matcher and context.get("tool_name") != matcher:
                continue

            if not is_trusted:
                # 非信任模式下跳过命令执行钩子
                if hook.get("type") == "command":
                    continue

            result = self._execute_hook(hook, context, env)
            exit_code = result.get("exit_code", 0)

            if exit_code == 1:
                blocked = True
            elif exit_code == 2:
                msgs = result.get("messages", [])
                injected_messages.extend(msgs)

            # 结构化输出支持
            if "updatedInput" in result:
                updated_context["tool_input"] = result["updatedInput"]
            if "additionalContext" in result:
                updated_context["additional_context"] = result["additionalContext"]

        return blocked, injected_messages, updated_context

    def _execute_hook(
        self,
        hook: dict,
        context: dict,
        env: dict | None
    ) -> dict:
        """执行单个钩子"""
        hook_type = hook.get("type", "command")

        if hook_type == "command":
            command = hook.get("command", "")
            if not command:
                return {"exit_code": 0}

            # 准备环境变量
            hook_env = os.environ.copy()
            if env:
                hook_env.update(env)
            hook_env["TOOL_NAME"] = context.get("tool_name", "")
            hook_env["TOOL_INPUT"] = json.dumps(context.get("tool_input", {}))
            hook_env["TOOL_OUTPUT"] = context.get("tool_output", "")

            try:
                result = run_command(
                    command,
                    cwd=WORKDIR,
                    timeout_seconds=30,
                    max_stdout_chars=20000,
                    max_stderr_chars=5000,
                    permission_level="shell_safe",
                    env=hook_env,
                )
                stdout = str(result.get("stdout") or "")
                try:
                    return json.loads(stdout)
                except json.JSONDecodeError:
                    return {"exit_code": int(result.get("exit_code") or 0), "output": stdout}
            except Exception as e:
                return {"exit_code": 0, "error": str(e)}

        return {"exit_code": 0}


__all__ = ["HookManager", "TRUST_MARKER"]
