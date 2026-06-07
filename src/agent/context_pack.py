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
    conversation_summary: str = ""
    execution_summary: str = ""
    workspace_summary: dict = field(default_factory=dict)
    relevant_files: list[str] = field(default_factory=list)
    selected_files: list[dict] = field(default_factory=list)
    recent_changes: list[str] = field(default_factory=list)
    file_outlines: list[dict] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    recent_failures: list[dict] = field(default_factory=list)
    recovery_context: dict = field(default_factory=dict)
    user_preferences: list[str] = field(default_factory=list)
    selected_memories: list[dict] = field(default_factory=list)
    omitted_memories: list[dict] = field(default_factory=list)
    memory_budget: dict = field(default_factory=dict)
    selected_skills: list[str] = field(default_factory=list)
    selected_skill_details: list[dict] = field(default_factory=list)
    omitted_skills: list[dict] = field(default_factory=list)
    skill_budget: dict = field(default_factory=dict)
    current_plan: list[dict] = field(default_factory=list)
    turn_context: dict = field(default_factory=dict)
    tool_policy: dict = field(default_factory=dict)
    selection_reasons: list[str] = field(default_factory=list)
    omitted: list[dict] = field(default_factory=list)
    budget_report: dict = field(default_factory=dict)
    token_budget: dict = field(default_factory=lambda: {"max_tokens": 12000, "used_tokens_estimate": 0})
    context_debug: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_summary": self.task_summary,
            "conversation_summary": self.conversation_summary,
            "execution_summary": self.execution_summary,
            "workspace_summary": self.workspace_summary,
            "relevant_files": self.relevant_files,
            "selected_files": self.selected_files,
            "recent_changes": self.recent_changes,
            "file_outlines": self.file_outlines,
            "symbols": self.symbols,
            "recent_failures": self.recent_failures,
            "recovery_context": self.recovery_context,
            "user_preferences": self.user_preferences,
            "selected_memories": self.selected_memories,
            "omitted_memories": self.omitted_memories,
            "memory_budget": self.memory_budget,
            "selected_skills": self.selected_skills,
            "selected_skill_details": self.selected_skill_details,
            "omitted_skills": self.omitted_skills,
            "skill_budget": self.skill_budget,
            "current_plan": self.current_plan,
            "turn_context": self.turn_context,
            "tool_policy": self.tool_policy,
            "selection_reasons": self.selection_reasons,
            "omitted": self.omitted,
            "budget_report": self.budget_report,
            "token_budget": self.token_budget,
            "context_debug": self.context_debug,
        }

    def to_text(self) -> str:
        """Generate the runtime instruction block for the LLM system prompt."""
        lines = ["【nanoCursor 上下文包】", ""]
        lines.append(f"任务: {self.task_summary}")

        if self.conversation_summary:
            lines.append(f"会话摘要: {self.conversation_summary}")

        if self.execution_summary:
            lines.append(f"运行摘要: {self.execution_summary}")

        if self.workspace_summary:
            ws = self.workspace_summary
            lines.append(f"项目: {ws.get('path', '')} "
                         f"(文件: {ws.get('total_files', 0)}, "
                         f"测试: {ws.get('test_count', 0)})")

        if self.relevant_files:
            lines.append(f"相关文件 ({len(self.relevant_files)}): "
                         + ", ".join(self.relevant_files[:10]))

        if self.selected_files:
            lines.append("相关文件选择依据:")
            for item in self.selected_files[:8]:
                reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
                reason_text = "；".join(str(reason) for reason in reasons[:3]) or "未记录原因"
                lines.append(
                    f"  - {item.get('path', '')} "
                    f"(score={item.get('relevance_score', 0)}, mode={item.get('mode', 'outline')}): "
                    f"{reason_text}"
                )

        if self.selection_reasons:
            lines.append("选择摘要:")
            for reason in self.selection_reasons[:6]:
                lines.append(f"  - {reason}")

        if self.omitted:
            lines.append("已裁剪上下文:")
            for item in self.omitted[:8]:
                lines.append(
                    f"  - {item.get('kind', 'context')} {item.get('path', '')}: "
                    f"{item.get('reason', 'budget limit')}"
                )

        if self.recent_changes:
            lines.append(f"最近变更: {', '.join(self.recent_changes[:8])}")

        if self.file_outlines:
            lines.append("文件大纲:")
            for item in self.file_outlines[:8]:
                symbols = item.get("symbols") if isinstance(item.get("symbols"), list) else []
                symbol_text = ", ".join(
                    f"{sym.get('type', 'symbol')} {sym.get('name', '')}@{sym.get('lineno', 0)}"
                    for sym in symbols[:6]
                    if isinstance(sym, dict)
                )
                lines.append(
                    f"  - {item.get('path', '')} [{item.get('language', 'text')}, {item.get('role', 'source')}]: "
                    f"{symbol_text or '无结构化符号'}"
                )

        if self.symbols:
            lines.append(f"关键符号: {', '.join(self.symbols[:15])}")

        if self.selected_skills:
            lines.append(f"启用 Skills: {', '.join(self.selected_skills)}")
            for skill in self.selected_skill_details[:5]:
                if not isinstance(skill, dict):
                    continue
                reasons = skill.get("selection_reasons") if isinstance(skill.get("selection_reasons"), list) else []
                permissions = skill.get("tool_permissions") if isinstance(skill.get("tool_permissions"), list) else []
                reason_text = "；".join(str(reason) for reason in reasons[:3]) or "execution plan"
                permission_text = f"；权限建议: {', '.join(str(item) for item in permissions[:5])}" if permissions else ""
                lines.append(
                    f"  - {skill.get('id', skill.get('name', 'skill'))}: "
                    f"{reason_text}{permission_text}"
                )
        if self.omitted_skills:
            lines.append("未注入 Skills:")
            for skill in self.omitted_skills[:6]:
                if not isinstance(skill, dict):
                    continue
                lines.append(
                    f"  - {skill.get('id', skill.get('name', 'skill'))}: "
                    f"{skill.get('reason', 'not selected')}"
                )

        if self.current_plan:
            lines.append("当前计划:")
            for item in self.current_plan[:8]:
                lines.append(f"  - {item.get('title', '')}: {item.get('description', '')}")

        if self.tool_policy:
            mode = self.tool_policy.get("mode") or "default"
            risk = self.tool_policy.get("risk_level") or "unknown"
            lines.append(f"工具策略: mode={mode}, risk={risk}")
            for label, key in (
                ("允许", "allowed_tools"),
                ("拒绝", "denied_tools"),
                ("需审批", "approval_required"),
                ("推荐", "recommended_tools"),
            ):
                tools = self.tool_policy.get(key)
                if isinstance(tools, list) and tools:
                    lines.append(f"  - {label}: {', '.join(str(tool) for tool in tools[:16])}")
            approval_levels = self.tool_policy.get("approval_required_levels")
            if isinstance(approval_levels, list) and approval_levels:
                lines.append(f"  - 需审批权限级别: {', '.join(str(level) for level in approval_levels[:12])}")

        if self.turn_context:
            lines.append("本轮观察:")
            step = self.turn_context.get("step")
            if step:
                lines.append(f"  - loop_step: {step}")
            active_task = self.turn_context.get("active_task")
            if isinstance(active_task, dict) and active_task:
                task_bits = [
                    str(active_task.get("id") or ""),
                    str(active_task.get("title") or ""),
                    str(active_task.get("status") or ""),
                    str(active_task.get("agent_role") or active_task.get("agent") or ""),
                ]
                lines.append("  - active_task: " + " | ".join(bit for bit in task_bits if bit))
                if active_task.get("goal"):
                    lines.append(f"    goal: {str(active_task.get('goal'))[:240]}")
                acceptance = active_task.get("acceptance")
                if isinstance(acceptance, list) and acceptance:
                    lines.append("    acceptance:")
                    for item in acceptance[:5]:
                        if isinstance(item, dict):
                            text = item.get("description") or item.get("title") or item.get("content")
                            if text:
                                lines.append(f"      * {str(text)[:240]}")
                evidence = active_task.get("recent_evidence")
                if isinstance(evidence, list) and evidence:
                    lines.append("    recent_evidence:")
                    for item in evidence[:5]:
                        if isinstance(item, dict):
                            text = item.get("content") or item.get("title") or item.get("description")
                            path = item.get("path") or ""
                            if text or path:
                                lines.append(f"      * {str(text or item.get('kind') or 'evidence')[:240]} {path}".strip())
            failed_tasks = self.turn_context.get("failed_tasks")
            if isinstance(failed_tasks, list) and failed_tasks:
                lines.append("  - failed_or_blocked_tasks:")
                for task in failed_tasks[:5]:
                    if not isinstance(task, dict):
                        continue
                    lines.append(
                        f"    * [{task.get('status', 'failed')}] "
                        f"{task.get('id', '')} {task.get('title', '')}".strip()
                    )
            counts = self.turn_context.get("task_status_counts")
            if isinstance(counts, dict) and counts:
                lines.append(f"  - task_status_counts: {counts}")
            changed_files = self.turn_context.get("changed_files")
            if isinstance(changed_files, list) and changed_files:
                lines.append(f"  - turn_changed_files: {', '.join(str(path) for path in changed_files[:10])}")
            tool_results = self.turn_context.get("recent_tool_results")
            if isinstance(tool_results, list) and tool_results:
                lines.append("  - recent_tool_results:")
                for item in tool_results[:5]:
                    if not isinstance(item, dict):
                        continue
                    title = item.get("title") or item.get("type") or "tool_result"
                    target = item.get("target") or item.get("path") or item.get("task_id") or ""
                    status = item.get("status") or item.get("result") or ""
                    lines.append(f"    * {title}: {status} {target}".strip())

        if self.recent_failures:
            lines.append(f"最近失败 ({len(self.recent_failures)}):")
            display_failures = sorted(
                self.recent_failures,
                key=lambda item: bool(item.get("related_files")) if isinstance(item, dict) else False,
                reverse=True,
            )
            for f in display_failures[:3]:
                cat = f.get("category", "unknown")
                summary = f.get("summary", "")
                related_files = f.get("related_files") if isinstance(f.get("related_files"), list) else []
                suffix = f"；关联文件: {', '.join(str(path) for path in related_files[:4])}" if related_files else ""
                lines.append(f"  - [{cat}] {summary}{suffix}")

        if self.recovery_context:
            lines.append(f"恢复上下文: status={self.recovery_context.get('status', 'unknown')}")
            actions = self.recovery_context.get("actions")
            if isinstance(actions, list) and actions:
                lines.append("建议恢复动作:")
                for action in actions[:5]:
                    if not isinstance(action, dict):
                        continue
                    title = action.get("title") or action.get("id") or "recovery action"
                    detail = action.get("detail") or ""
                    risk = action.get("risk_level") or "safe"
                    lines.append(f"  - [{risk}] {title}: {str(detail)[:300]}")

        if self.selected_memories:
            lines.append("受控记忆:")
            for memory in self.selected_memories[:8]:
                if not isinstance(memory, dict):
                    continue
                summary = memory.get("summary") or memory.get("content") or ""
                reasons = memory.get("reasons") if isinstance(memory.get("reasons"), list) else []
                lines.append(
                    f"  - [{memory.get('scope', 'workspace')}/{memory.get('kind', 'memory')}] "
                    f"{str(summary)[:400]} "
                    f"(score={memory.get('score', 0)}; {', '.join(str(reason) for reason in reasons[:3])})"
                )

        if self.user_preferences:
            lines.append(f"用户偏好: {', '.join(self.user_preferences[:5])}")

        budget = self.token_budget
        report = self.budget_report if isinstance(self.budget_report, dict) else {}
        lines.append(
            f"Token 预算: {budget.get('max_tokens', 12000)} max, "
            f"预估已用 {self.estimate_tokens()}"
        )
        if report:
            lines.append(
                f"预算取舍: included={report.get('included_file_count', 0)}, "
                f"trimmed={report.get('trimmed_file_count', 0)}, "
                f"utilization={report.get('utilization', 0)}"
            )

        return "\n".join(lines)

    def estimate_tokens(self) -> int:
        """Rough token estimate of the context pack content (~3 chars per token)."""
        text_parts = [
            self.task_summary,
            self.conversation_summary,
            self.execution_summary,
            str(self.workspace_summary),
            " ".join(self.relevant_files),
            str(self.selected_files),
            " ".join(self.recent_changes),
            str(self.file_outlines),
            " ".join(self.symbols),
            str(self.recent_failures),
            str(self.recovery_context),
            str(self.selected_memories),
            str(self.omitted_memories),
            str(self.memory_budget),
            " ".join(self.selected_skills),
            str(self.selected_skill_details),
            str(self.omitted_skills),
            str(self.skill_budget),
            " ".join(self.user_preferences),
            str(self.current_plan),
            str(self.turn_context),
            str(self.tool_policy),
            " ".join(self.selection_reasons),
            str(self.omitted),
            str(self.budget_report),
            str(self.context_debug),
        ]
        total_chars = sum(len(p) for p in text_parts)
        return total_chars // 3
