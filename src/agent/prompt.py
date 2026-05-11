"""
System Prompt Builder - 借鉴 s10_system_prompt.py

将系统提示从硬编码字符串改为 sections 管道式构建。
支持 DYNAMIC_BOUNDARY 缓存静态前缀，动态内容每轮注入。
"""

from pathlib import Path
from typing import Optional
from src.infra.config import WORKSPACE_DIR

WORKDIR = Path(WORKSPACE_DIR)


DYNAMIC_BOUNDARY = "=== DYNAMIC_BOUNDARY ==="


def _build_core() -> str:
    """核心角色指令"""
    return f"""你是一个自动编程助手，在 {WORKDIR} 工作。

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
"""


def _build_tool_listing(tools: list) -> str:
    """工具列表"""
    if not tools:
        return ""
    lines = ["【可用工具】"]
    for t in tools:
        name = t.get("name", "")
        desc = t.get("description", "")
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


def _build_dynamic_context() -> str:
    """动态上下文（每轮变化）"""
    from datetime import datetime
    import platform
    return f"""【当前环境】
- 日期: {datetime.now().strftime('%Y-%m-%d')}
- 工作目录: {WORKDIR}
- 平台: {platform.system()}
"""


class SystemPromptBuilder:
    """
    系统提示构建器 - sections 管道式
    """

    def __init__(self, tools: list = None, skills_dir: Path = None):
        self.tools = tools or []
        self.skills_dir = skills_dir or (WORKDIR / "skills")
        self._static_cache: str | None = None

    def build(self) -> str:
        """构建完整系统提示"""
        sections = [
            _build_core(),
            _build_tool_listing(self.tools),
            _build_dynamic_context(),
        ]
        return "\n\n".join(sections)

    def build_static(self) -> str:
        """构建静态部分（可缓存）"""
        if self._static_cache:
            return self._static_cache
        sections = [
            _build_core(),
            _build_tool_listing(self.tools),
        ]
        self._static_cache = "\n\n".join(sections)
        return self._static_cache

    def build_dynamic(self) -> str:
        """构建动态部分"""
        return _build_dynamic_context()

    def build_with_reminder(self, extra: str = "") -> str:
        """构建带每轮提醒的系统提示"""
        parts = [self.build_static(), DYNAMIC_BOUNDARY, self.build_dynamic()]
        if extra:
            parts.append(f"\n\n【当前任务】\n{extra}")
        return "\n\n".join(parts)

    def clear_cache(self):
        """清除静态缓存"""
        self._static_cache = None


__all__ = ["SystemPromptBuilder", "DYNAMIC_BOUNDARY"]