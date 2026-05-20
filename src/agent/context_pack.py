"""Structured context object for Agent decision-making."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContextPack:
    """Structured context replacing raw prompt concatenation.

    Both machine-readable (to_dict for API/frontend) and human-readable
    (to_text for LLM system prompt).
    """

    task_summary: str = ""
    workspace_summary: dict = field(default_factory=dict)
    relevant_files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    recent_failures: list[dict] = field(default_factory=list)
    user_preferences: list[str] = field(default_factory=list)
    selected_skills: list[str] = field(default_factory=list)
    token_budget: dict = field(default_factory=lambda: {"max_tokens": 12000, "used_tokens_estimate": 0})

    def to_dict(self) -> dict:
        return {
            "task_summary": self.task_summary,
            "workspace_summary": self.workspace_summary,
            "relevant_files": self.relevant_files,
            "symbols": self.symbols,
            "recent_failures": self.recent_failures,
            "user_preferences": self.user_preferences,
            "selected_skills": self.selected_skills,
            "token_budget": self.token_budget,
        }

    def to_text(self) -> str:
        """Generate the runtime instruction block for the LLM system prompt."""
        lines = ["【AgentHub 上下文包】", ""]
        lines.append(f"任务: {self.task_summary}")

        if self.workspace_summary:
            ws = self.workspace_summary
            lines.append(f"项目: {ws.get('path', '')} "
                         f"(文件: {ws.get('total_files', 0)}, "
                         f"测试: {ws.get('test_count', 0)})")

        if self.relevant_files:
            lines.append(f"相关文件 ({len(self.relevant_files)}): "
                         + ", ".join(self.relevant_files[:10]))

        if self.symbols:
            lines.append(f"关键符号: {', '.join(self.symbols[:15])}")

        if self.selected_skills:
            lines.append(f"启用 Skills: {', '.join(self.selected_skills)}")

        if self.recent_failures:
            lines.append(f"最近失败 ({len(self.recent_failures)}):")
            for f in self.recent_failures[:3]:
                cat = f.get("category", "unknown")
                summary = f.get("summary", "")
                lines.append(f"  - [{cat}] {summary}")

        if self.user_preferences:
            lines.append(f"用户偏好: {', '.join(self.user_preferences[:5])}")

        budget = self.token_budget
        lines.append(
            f"Token 预算: {budget.get('max_tokens', 12000)} max, "
            f"预估已用 {self.estimate_tokens()}"
        )

        return "\n".join(lines)

    def estimate_tokens(self) -> int:
        """Rough token estimate of the context pack content (~3 chars per token)."""
        text_parts = [
            self.task_summary,
            str(self.workspace_summary),
            " ".join(self.relevant_files),
            " ".join(self.symbols),
            " ".join(self.selected_skills),
            " ".join(self.user_preferences),
        ]
        total_chars = sum(len(p) for p in text_parts)
        return total_chars // 3
