"""
Learner - 自我进化系统，从工具调用的成功/失败中自动学习。

FailureLearner: 工具失败时自动记录错误模式，后续会话检索并预防。
ExperienceLearner: 记录成功的工具调用链，形成可复用的"方案"（episode），
                  跨会话检索相关经验。

核心概念：
- Episode: 一组有序的工具调用序列，以成功（测试通过）或失败（多次错误）结束
- Pattern: 从 Episode 中提取的可复用抽象，如 "flask-login-setup"
- 每次工具失败时，增加对应错误模式的重要性
- 每次成功的工具链被记录为 project memory，后续相似任务可检索
"""

import re
import time
from typing import Optional

from src.memory.manager import get_memory_manager, MEMORY_CATEGORIES
from src.infra.logger import logger


def extract_error_signature(output: str) -> str:
    """从错误输出中提取简短可搜索的错误签名"""
    m = re.search(r'(\w+Error):\s*(.+?)(?:\n|$)', output)
    if m:
        return f"{m.group(1)}: {m.group(2)[:80]}"
    patterns = [
        (r"(command not found)", "Command not found"),
        (r"(Permission denied)", "Permission denied"),
        (r"(No such file or directory)", "No such file"),
        (r"(ModuleNotFoundError)", "Module not found"),
        (r"(SyntaxError)[:\s]*(.+?)(?:\n|$)", "Syntax"),
        (r"(ImportError)[:\s]*(.+?)(?:\n|$)", "Import"),
        (r"(Timeout)", "Timeout"),
    ]
    for pat, label in patterns:
        m = re.search(pat, output, re.IGNORECASE)
        if m:
            if m.lastindex and m.lastindex >= 2:
                return f"{label}: {m.group(2)[:80]}"
            return label
    clean = output.strip().replace('\n', ' ')[:100]
    return clean if clean else "Unknown error"


def _build_error_tags(tool_name: str, error_signature: str) -> list[str]:
    """为错误记忆构建搜索标签"""
    tags = [tool_name, "error"]
    for keyword in ["Error", "failed", "permission", "timeout", "not found",
                     "denied", "invalid", "missing", "syntax"]:
        if keyword.lower() in error_signature.lower():
            tags.append(keyword.lower().replace(" ", "_"))
    return tags


def _search_similar_failure(tool_name: str, error_signature: str) -> Optional[dict]:
    """搜索是否已有相同类型的失败记录"""
    mm = get_memory_manager()
    results = mm.search(error_signature.split(":")[0] if ":" in error_signature else error_signature, limit=10)
    for r in results:
        if r.get("category") != "feedback":
            continue
        if tool_name in r.get("tags", []) and "error" in r.get("tags", []):
            return r
    return None


# ========== Episode / Pattern extraction ==========

def extract_episode_signature(tool_history: list[dict]) -> str:
    """从工具调用历史中提取 episode 签名。

    一个 episode 是: [read_file, edit_file, bash(test), ...] 的序列。
    签名是工具名的有序列表，如 "read_file>edit_file>bash"。
    """
    names = []
    for call in tool_history[-10:]:  # Last 10 calls
        name = call.get("tool", "")
        if name and name not in ("TodoWrite", "TodoList", "task_list", "read_inbox"):
            names.append(name)
    return ">".join(names) if names else "unknown"


def extract_episode_keywords(tool_history: list[dict], output: str) -> list[str]:
    """从 episode 中提取关键词用于检索。

    检查: 文件扩展名、框架名（flask/django/react/vue）、关键操作。
    """
    keywords = set()
    for call in tool_history[-10:]:
        tool = call.get("tool", "")
        inp = call.get("input", {})
        # Extract file extensions
        for key in ("path", "filepath"):
            if key in inp:
                ext = str(inp[key]).rsplit(".", 1)[-1] if "." in str(inp[key]) else ""
                if ext and len(ext) <= 6:
                    keywords.add(ext)
        # Extract framework/library names from bash pip/npm install
        if tool == "bash":
            cmd = str(inp.get("command", ""))
            for framework in ["flask", "django", "fastapi", "react", "vue", "express",
                            "pytest", "jest", "mocha", "sqlalchemy", "bcrypt", "jwt"]:
                if framework in cmd.lower():
                    keywords.add(framework)
    return list(keywords)[:8]


# ========== FailureLearner ==========

class FailureLearner:
    """从工具失败中自动学习"""

    def __init__(self):
        self._recent_failures: list[dict] = []

    def on_tool_failure(
        self, tool_name: str, tool_input: dict, error_output: str, session_id: str = "",
    ) -> None:
        """工具失败时被调用：提取错误模式，自动创建/更新记忆"""
        error_sig = extract_error_signature(error_output)
        if not error_sig or len(error_sig) < 3:
            return

        mm = get_memory_manager()
        existing = _search_similar_failure(tool_name, error_sig)

        if existing:
            new_imp = min(existing.get("importance", 1) + 2, 10)
            mm.update(existing["id"], importance=new_imp)
            mm.inc_access(existing["id"])
            logger.info(f"[Learner] Updated existing failure: {tool_name} -> {error_sig[:60]} (imp={new_imp})")
        else:
            context = self._summarize_input(tool_input)
            content = (
                f"工具 {tool_name} 失败\n\n"
                f"**输入**: {context}\n\n"
                f"**错误**: {error_sig}\n\n"
                f"**完整输出（前500字）**:\n```\n{error_output[:500]}\n```"
            )
            tags = _build_error_tags(tool_name, error_sig)
            mm.save(
                category="feedback", content=content, importance=7,
                tags=tags, session_id=session_id,
            )
            logger.info(f"[Learner] Recorded new failure: {tool_name} -> {error_sig[:60]}")

        self._recent_failures.append({
            "tool": tool_name, "error": error_sig, "time": time.time(),
        })
        if len(self._recent_failures) > 20:
            self._recent_failures = self._recent_failures[-20:]

    def on_tool_success(self, tool_name: str, tool_input: dict, output: str) -> None:
        """工具成功时被调用：记录成功模式供参考"""
        if len(output) < 10:
            return
        self._recent_failures.append({
            "tool": tool_name, "success": True,
            "output_preview": output[:100], "time": time.time(),
        })
        if len(self._recent_failures) > 20:
            self._recent_failures = self._recent_failures[-20:]

    def build_learning_context(self, limit: int = 3) -> str:
        """为系统提示构建'最近学到的教训'段落"""
        mm = get_memory_manager()
        memories = mm.get(category="feedback", min_importance=7, limit=limit)
        if not memories:
            memories = mm.get(category="feedback", min_importance=5, limit=limit)
        if not memories:
            return ""

        lines = ["【从过去学到的教训】"]
        for m in memories:
            imp = m.get("importance", 0)
            tags = ", ".join(m.get("tags", [])[:3])
            content_preview = m.get("content", "")[:120].replace("\n", " ")
            lines.append(f"- ⚠️ [{tags}] {content_preview} (重要性: {imp})")
        return "\n".join(lines)

    def _summarize_input(self, tool_input: dict) -> str:
        parts = []
        for k, v in list(tool_input.items())[:3]:
            val_str = str(v).replace("\n", " ")[:60]
            parts.append(f"{k}={val_str}")
        return ", ".join(parts) if parts else "(no input)"


# ========== ExperienceLearner ==========

class ExperienceLearner:
    """Record and retrieve successful tool call chains as reusable patterns.

    When a task completes successfully (tests pass, file changes verified),
    the full tool call chain is saved as an "episode" memory. On future tasks,
    matching episodes are retrieved and injected as context to guide the agent.
    """

    def __init__(self):
        self._current_episode: list[dict] = []
        self._episode_active: bool = False

    def start_episode(self, task_description: str = "") -> None:
        """Start recording a new episode."""
        self._current_episode = []
        self._episode_active = True
        if task_description:
            self._current_episode.append({"tool": "__task__", "input": {"description": task_description}, "time": time.time()})

    def record_call(self, tool_name: str, tool_input: dict, output: str) -> None:
        """Record a tool call in the current episode."""
        if not self._episode_active:
            return
        self._current_episode.append({
            "tool": tool_name,
            "input": {k: str(v)[:100] for k, v in tool_input.items()},
            "output_preview": output[:200],
            "success": not output.startswith("Error:"),
            "time": time.time(),
        })

    def complete_episode(self, outcome: str = "success", summary: str = "") -> Optional[str]:
        """
        Finish the episode and persist it if it contains meaningful work.

        An episode is considered "meaningful" if it has:
        - At least 2 tool calls
        - At least one file modification (write_file or edit_file)
        - A successful outcome

        Returns the memory ID if saved, None otherwise.
        """
        self._episode_active = False
        if len(self._current_episode) < 2:
            self._current_episode = []
            return None

        # Check for meaningful work
        has_file_change = any(
            c.get("tool") in ("write_file", "edit_file")
            for c in self._current_episode
        )
        if not has_file_change and outcome == "success":
            self._current_episode = []
            return None

        signature = extract_episode_signature(self._current_episode)
        keywords = extract_episode_keywords(self._current_episode, summary)

        # Build content
        steps = []
        for i, call in enumerate(self._current_episode, 1):
            tool = call.get("tool", "?")
            inp = call.get("input", {})
            inp_summary = ", ".join(f"{k}={v[:60]}" for k, v in list(inp.items())[:2])
            status = "✓" if call.get("success", True) else "✗"
            steps.append(f"  {i}. [{status}] {tool}({inp_summary})")

        content = (
            f"成功方案: {signature}\n\n"
            f"**关键词**: {', '.join(keywords) if keywords else '无'}\n\n"
            f"**步骤**:\n" + "\n".join(steps) + "\n\n"
            f"**结果**: {outcome}\n"
        )
        if summary:
            content += f"\n**摘要**: {summary[:300]}"

        mm = get_memory_manager()
        entry = mm.save(
            category="project",
            content=content,
            importance=6,  # High enough to auto-load in related sessions
            tags=["episode", "success", *keywords],
        )

        self._current_episode = []
        logger.info(f"[ExperienceLearner] Saved episode: {signature} (id={entry.get('id', '?')[:8]})")
        return entry.get("id")

    def retrieve_relevant(self, current_context: str, limit: int = 3) -> list[dict]:
        """Find past episodes relevant to the current task."""
        mm = get_memory_manager()
        results = mm.search(current_context, limit=limit * 2)
        episodes = []
        for r in results:
            tags = r.get("tags", [])
            if "episode" in tags and "success" in tags:
                episodes.append(r)
        return episodes[:limit]

    def build_experience_context(self, task_description: str, limit: int = 2) -> str:
        """Build a context string of relevant past experiences."""
        episodes = self.retrieve_relevant(task_description, limit)
        if not episodes:
            return ""

        lines = ["【相关历史经验】以下是从之前项目中检索到的成功方案，可供参考："]
        for ep in episodes:
            content = ep.get("content", "")[:400].replace("\n", " ")
            tags = ", ".join(ep.get("tags", [])[:5])
            lines.append(f"- [{tags}] {content}")
        return "\n".join(lines)


# ========== Global singletons ==========

_learner: Optional[FailureLearner] = None
_experience_learner: Optional[ExperienceLearner] = None


def get_learner() -> FailureLearner:
    global _learner
    if _learner is None:
        _learner = FailureLearner()
    return _learner


def get_experience_learner() -> ExperienceLearner:
    global _experience_learner
    if _experience_learner is None:
        _experience_learner = ExperienceLearner()
    return _experience_learner


__all__ = [
    "FailureLearner", "ExperienceLearner",
    "get_learner", "get_experience_learner",
    "extract_error_signature",
    "extract_episode_signature", "extract_episode_keywords",
]
