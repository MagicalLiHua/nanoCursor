"""Formal tool policy with budgets and risk levels."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolPolicy:
    """Tool access policy with budget enforcement."""

    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    approval_required: list[str] = field(default_factory=list)
    budgets: dict = field(default_factory=lambda: {
        "max_tool_calls": 40,
        "max_file_writes": 8,
        "max_test_runs": 3,
    })
    risk_level: str = "medium"

    def check(self, tool_name: str) -> bool:
        """Return True if tool is allowed, False if denied (or not in allowed list)."""
        if tool_name in self.denied_tools:
            return False
        if not self.allowed_tools:
            return True  # empty = allow all (except denied)
        return tool_name in self.allowed_tools

    def requires_approval(self, tool_name: str) -> bool:
        return tool_name in self.approval_required

    def within_budget(self, tool_call_count: int, file_write_count: int, test_run_count: int = 0) -> bool:
        b = self.budgets
        max_calls = b.get("max_tool_calls", None)
        if max_calls is not None and tool_call_count >= max_calls:
            return False
        max_writes = b.get("max_file_writes", None)
        if max_writes is not None and file_write_count >= max_writes:
            return False
        max_tests = b.get("max_test_runs", None)
        if max_tests is not None and test_run_count >= max_tests:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "allowed_tools": self.allowed_tools,
            "denied_tools": self.denied_tools,
            "approval_required": self.approval_required,
            "budgets": self.budgets,
            "risk_level": self.risk_level,
        }


# Pre-built policies for each strategy
def policy_for_strategy(strategy_id: str) -> ToolPolicy:
    if strategy_id == "small_patch":
        return ToolPolicy(
            allowed_tools=["read_file", "search_codebase", "edit_file", "write_file",
                           "list_directory", "task_create", "task_update", "task_list"],
            denied_tools=["delete_file"],
            approval_required=[],
            budgets={"max_tool_calls": 15, "max_file_writes": 3, "max_test_runs": 2},
            risk_level="low",
        )
    if strategy_id == "bug_fix":
        return ToolPolicy(
            allowed_tools=["read_file", "search_codebase", "edit_file", "write_file",
                           "list_directory", "bash", "task_create", "task_update", "task_list",
                           "run_tests", "git_status", "git_diff"],
            denied_tools=["delete_file"],
            approval_required=["bash"],
            budgets={"max_tool_calls": 30, "max_file_writes": 5, "max_test_runs": 5},
            risk_level="medium",
        )
    if strategy_id == "refactor":
        return ToolPolicy(
            allowed_tools=["read_file", "search_codebase", "edit_file", "write_file",
                           "list_directory", "bash", "task_create", "task_update", "task_list",
                           "run_tests", "project_context", "git_status", "git_diff"],
            denied_tools=["delete_file"],
            approval_required=["bash", "write_file"],
            budgets={"max_tool_calls": 50, "max_file_writes": 12, "max_test_runs": 5},
            risk_level="medium",
        )
    if strategy_id == "docs_only":
        return ToolPolicy(
            allowed_tools=["read_file", "search_codebase", "list_directory",
                           "write_file", "task_create", "task_update", "task_list", "project_context"],
            denied_tools=["bash", "edit_file", "delete_file", "run_tests"],
            approval_required=[],
            budgets={"max_tool_calls": 10, "max_file_writes": 3, "max_test_runs": 0},
            risk_level="low",
        )
    if strategy_id == "analysis_only":
        return ToolPolicy(
            allowed_tools=["read_file", "search_codebase", "list_directory",
                           "project_context", "task_create", "task_update", "task_list"],
            denied_tools=["write_file", "edit_file", "bash", "delete_file", "run_tests"],
            approval_required=[],
            budgets={"max_tool_calls": 20, "max_file_writes": 0, "max_test_runs": 0},
            risk_level="low",
        )
    # feature_delivery (default)
    return ToolPolicy(
        allowed_tools=["read_file", "search_codebase", "edit_file", "write_file",
                       "list_directory", "bash", "task_create", "task_update", "task_list",
                       "run_tests", "project_context", "add_memory", "recall_memories", "git_status", "git_diff"],
        denied_tools=["delete_file"],
        approval_required=["bash"],
        budgets={"max_tool_calls": 60, "max_file_writes": 10, "max_test_runs": 8},
        risk_level="medium",
    )
