from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanocursor.permissions.dangerous import DangerousCommandDetector, is_safe_command
from nanocursor.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from nanocursor.permissions.rules import RuleEngine, extract_content
from nanocursor.permissions.sandbox import PathSandbox
from nanocursor.tools.base import Tool

_PLAN_MODE_ALLOWED_TOOLS = frozenset({"Agent", "ToolSearch", "AskUserQuestion", "ExitPlanMode"})


@dataclass
class Decision:
    effect: DecisionEffect
    reason: str
    source: str = "mode_fallback"

    @property
    def review_eligible(self) -> bool:
        return self.effect == "ask" and self.source in {"mode_fallback", "shell_uncertain"}


class PermissionChecker:


    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
        sandbox_enabled: bool = False,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
        self.plan_file_path: str = ""
        # OS 级沙箱是否启用（开启后命令类工具可自动放行，因为内核会兜底）
        self.sandbox_enabled = sandbox_enabled
        # Layer 4b: 会话级 allow-always 集合（内存中，不持久化）
        # 存放格式为 "ToolName:pattern"，用户选择 "don't ask again" 时记录
        self._session_allowed: set[str] = set()


    def add_session_allow(self, tool_name: str, content: str) -> None:
        """将工具+内容模式加入会话级放行集合（Layer 4b）。

        比持久化规则引擎优先级更高，但不写入磁盘——会话结束即消失。
        """
        key = f"{tool_name}:{content}"
        self._session_allowed.add(key)

    def _check_session_allowed(self, tool_name: str, content: str) -> bool:
        """检查是否匹配会话级放行记录。"""
        if not self._session_allowed:
            return False
        key = f"{tool_name}:{content}"
        if key in self._session_allowed:
            return True
        # 前缀匹配：已记录的 pattern 可能带通配尾缀
        for allowed in self._session_allowed:
            if allowed.endswith("*") and key.startswith(allowed[:-1]):
                return True
        return False

    @staticmethod
    def describe_tool_action(tool_name: str, arguments: dict[str, Any]) -> str:
        """为 HITL 确认生成人类可读的操作描述（对齐 Go 版 ExtractContent + formatToolArgs）。"""
        if tool_name == "ManageMCP":
            import json
            from copy import deepcopy
            config = deepcopy(arguments.get("config") or {})
            for field in ("env", "headers"):
                config[field] = {key: value if "${" in value else "[configured]"
                                 for key, value in config.get(field, {}).items()}
            detail = json.dumps(config, ensure_ascii=False, indent=2) if config else ""
            return (f"MCP {arguments.get('action')}: {arguments.get('name')}\n"
                    "保存到用户配置，影响后续启动及其他项目。\n"
                    "本地 MCP 程序不使用 Bash 沙箱；远程服务仅连接/断开。\n" + detail)
        content = extract_content(tool_name, arguments)
        if content:
            return content
        # 无法从标准字段提取时，拼接参数摘要
        parts = []
        for k, v in arguments.items():
            sv = str(v)
            if len(sv) > 80:
                sv = sv[:77] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts) if parts else tool_name


    def check(self, tool: Tool, arguments: dict[str, Any], *, smart: bool = False) -> Decision:
        content = extract_content(tool.name, arguments)

        # Explicit restrictions precede convenience shortcuts and permission modes.
        rule_result = self.rule_engine.evaluate(tool.name, content)
        if rule_result == "deny":
            return Decision(effect="deny", reason="权限规则拒绝", source="explicit_deny")
        if tool.name == "ManageMCP" and arguments.get("action") != "list" and self.mode == PermissionMode.PLAN:
            return Decision("deny", "Plan mode: MCP configuration changes are unavailable", "mode_restriction")
        if tool.name == "Bash":
            hit, reason = self.detector.detect(content)
            if hit:
                return Decision(effect="deny", reason=f"危险命令拦截: {reason}", source="dangerous_pattern")
        if rule_result == "ask":
            return Decision(effect="ask", reason="权限规则要求确认", source="explicit_ask")
        if tool.name == "ManageMCP" and arguments.get("action") == "list":
            return Decision("allow", "Read-only MCP status", "safe_command")

        # Keep explicit restrictions on compound components in smart mode too.
        if smart and tool.name == "Bash":
            import re
            effects = [self.rule_engine.evaluate(tool.name, sub.strip())
                       for sub in re.split(r"&&|\|\||[;|&\n\r]", content) if sub.strip()]
            if "deny" in effects:
                return Decision("deny", "权限规则拒绝", "explicit_deny")
            if "ask" in effects:
                return Decision("ask", "权限规则要求确认", "explicit_ask")

        # Layer 0: Plan 模式例外放行
        if self.mode == PermissionMode.PLAN:
            if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
                return Decision(effect="allow", reason="Plan mode: allowed tool", source="mode_restriction")

        # Layer 1: 安全的只读命令（自动放行）
        if tool.name == "Bash" and is_safe_command(content or ""):
            return Decision(effect="allow", reason="Safe read-only command", source="safe_command")

        # Layer 1c: OS 沙箱自动放行
        # 沙箱开启时，命令类工具通过了危险命令检查后直接放行——
        # 内核级隔离会阻止越权写入，无需再弹确认。
        # 拆分复合命令逐条检查，防止通过命令拼接绕过权限检查，
        # deny 规则和 ask 规则不受沙箱影响。
        if self.sandbox_enabled and tool.name == "Bash" and not smart:
            import re
            subcommands = [s.strip() for s in re.split(r'\s*(?:&&|\|\||[;|&\n\r])\s*', content) if s.strip()]
            if not subcommands:
                subcommands = [content]
            has_ask = False
            for sub in subcommands:
                rule_result = self.rule_engine.evaluate(tool.name, sub)
                if rule_result == "deny":
                    return Decision(effect="deny", reason="权限规则拒绝", source="explicit_deny")
                if rule_result == "ask":
                    has_ask = True
            if has_ask:
                return Decision(effect="ask", reason="权限规则要求确认", source="explicit_ask")
            if any(ch in content for ch in "\n\r|;&<>$`(){}\\"):
                return Decision(effect="ask", reason="复合 Shell 语法需要确认", source="shell_uncertain")
            return Decision(effect="allow", reason="OS 沙箱自动放行", source="sandbox_allow")

        # Layer 2: 路径沙箱（仅文件类工具）
        path_content = arguments.get("path", ".") if tool.name in ("Glob", "Grep") else content
        if tool.category in ("read", "write") and path_content:
            ok, reason = self.sandbox.check(str(path_content))
            if not ok and self.mode != PermissionMode.BYPASS:
                return Decision(effect="ask", reason=f"路径沙箱拦截: {reason}", source="path_restriction")

        # Only the allocated plan file gets this convenience exception, and
        # only after explicit restrictions and the path sandbox have agreed.
        if (self.mode == PermissionMode.PLAN and tool.name in ("WriteFile", "EditFile")
                and self._is_plan_file(content)):
            return Decision(effect="allow", reason="Plan mode: plan file write", source="mode_restriction")

        # Layer 3: 规则引擎匹配
        rule_result = self.rule_engine.evaluate(tool.name, content)
        if rule_result == "allow":
            return Decision(effect="allow", reason="权限规则放行", source="explicit_allow")
        if rule_result == "deny":
            return Decision(effect="deny", reason="权限规则拒绝", source="explicit_deny")

        # Layer 4b: 会话级放行（内存中，优先于模式兜底）
        if self._check_session_allowed(tool.name, content or ""):
            return Decision(effect="allow", reason="会话级放行（session allow-always）", source="session_allow")

        # Layer 4: 权限模式兜底判定
        effect = mode_decide(self.mode, tool.category)
        if effect == "allow":
            return Decision(effect="allow", reason=f"权限模式 {self.mode.value} 放行", source="mode_allow")
        if effect == "deny":
            return Decision(effect="deny", reason=f"权限模式 {self.mode.value} 拒绝", source="mode_restriction")

        # Layer 5: 触发人工确认（HITL）
        return Decision(effect="ask", reason="需要用户确认",
                        source="mode_restriction" if self.mode == PermissionMode.PLAN else "mode_fallback")


    def _is_plan_file(self, target_path: str) -> bool:
        if not self.plan_file_path or not target_path:
            return False
        try:
            root = self.sandbox.project_root

            def checked_path(raw: str) -> Path | None:
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    path = root / path
                # Check before resolve(), otherwise a link to the real plan
                # would become indistinguishable from its allocated path.
                relative = path.relative_to(root)
                current = root
                # Walk before normalizing '..': a symlink followed by '..'
                # follows the link on disk and must not disappear lexically.
                for component in relative.parts:
                    current = current.parent if component == ".." else current / component
                    current.relative_to(root)
                    if current.is_symlink():
                        return None
                return current.resolve()

            target = checked_path(target_path)
            plan = checked_path(self.plan_file_path)
            return target is not None and plan is not None and target == plan
        except (OSError, RuntimeError, ValueError):
            return False
