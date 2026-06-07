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

from src.infra.logger import logger
from src.tools.tool_result import is_tool_error_output


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
    from src.api.services.memory_governance_service import list_memory_records

    results = list_memory_records(_runtime_workspace(), scope="workspace", status="active", limit=200)
    for r in results:
        if r.get("kind") != "failure_pattern":
            continue
        if tool_name in r.get("tags", []) and error_signature.split(":")[0].lower() in str(r.get("content", "")).lower():
            return r
    return None


def _runtime_workspace() -> str:
    try:
        from src.agent.engine import get_runtime_context

        workspace = get_runtime_context().get("workspace_dir")
        if workspace:
            return str(workspace)
    except Exception:
        pass
    from src.infra import config as config_module

    return str(config_module.WORKSPACE_DIR)


def _runtime_run_id() -> str:
    try:
        from src.agent.engine import get_runtime_context

        return str(get_runtime_context().get("thread_id") or "")
    except Exception:
        return ""


# ========== Episode / Pattern extraction ==========

def extract_episode_signature(tool_history: list[dict]) -> str:
    """从工具调用历史中提取 episode 签名。

    一个 episode 是: [read_file, edit_file, bash(test), ...] 的序列。
    签名是工具名的有序列表，如 "read_file>edit_file>bash"。
    """
    names = []
    for call in tool_history[-10:]:  # Last 10 calls
        name = call.get("tool", "")
        if name and name != "task_list":
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

        try:
            from src.api.services.memory_governance_service import create_memory_record, update_memory_record

            workspace = _runtime_workspace()
            existing = _search_similar_failure(tool_name, error_sig)

            if existing:
                new_imp = min(existing.get("importance", 1) + 2, 10)
                update_memory_record(workspace, existing["id"], importance=new_imp)
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
                run_id = session_id or _runtime_run_id()
                create_memory_record(
                    workspace,
                    scope="workspace",
                    kind="failure_pattern",
                    content=content,
                    source="failure_recovery",
                    confidence=0.55,
                    importance=7,
                    tags=tags,
                    source_ref=f"run:{run_id}" if run_id else "tool_failure",
                    evidence_refs=[f"run:{run_id}"] if run_id else [],
                    automatic=True,
                )
                logger.info(f"[Learner] Recorded new failure: {tool_name} -> {error_sig[:60]}")
        except Exception as exc:
            logger.warning(f"[Learner] Skipped unsafe or invalid failure memory: {exc}")

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
        """Legacy direct-prompt injection is disabled; ContextPack owns recall."""
        return ""

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
            "success": not is_tool_error_output(output),
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

        from src.api.services.memory_governance_service import create_memory_record

        run_id = _runtime_run_id()
        try:
            entry = create_memory_record(
                _runtime_workspace(),
                scope="workspace",
                kind="workflow_note",
                content=content,
                source="run_evidence",
                confidence=0.75,
                importance=6,
                tags=["episode", "success", *keywords],
                source_ref=f"run:{run_id}" if run_id else "experience_episode",
                evidence_refs=[f"run:{run_id}"] if run_id else [],
                automatic=True,
            )
        except Exception as exc:
            logger.warning(f"[ExperienceLearner] Skipped unsafe or invalid episode memory: {exc}")
            return None
        finally:
            self._current_episode = []

        logger.info(f"[ExperienceLearner] Saved episode: {signature} (id={entry.get('id', '?')[:8]})")
        return entry.get("id")

    def retrieve_relevant(self, current_context: str, limit: int = 3) -> list[dict]:
        """Find past episodes relevant to the current task."""
        from src.api.services.memory_selection_service import select_memories

        results = select_memories(
            _runtime_workspace(),
            prompt=current_context,
            budget_tokens=max(300, limit * 220),
            persist_audit=False,
        ).get("selected", [])
        episodes = []
        for r in results:
            tags = r.get("tags", [])
            if "episode" in tags and "success" in tags:
                episodes.append(r)
        return episodes[:limit]

    def build_experience_context(self, task_description: str, limit: int = 2) -> str:
        """Legacy direct-prompt injection is disabled; ContextPack owns recall."""
        return ""


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
