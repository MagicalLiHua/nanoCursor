"""RunBudget: track tool/file/test calls and detect budget exceeded."""

from __future__ import annotations

from dataclasses import dataclass, field

WRITE_TOOLS = frozenset({"write_file", "edit_file"})
TEST_TOOLS = frozenset({"run_tests", "bash"})


@dataclass
class RunBudget:
    max_tool_calls: int = 40
    max_file_writes: int = 8
    max_test_runs: int = 3
    tool_calls: int = 0
    file_writes: int = 0
    test_runs: int = 0
    _decisions: list[dict] = field(default_factory=list)

    def record_tool(self, tool_name: str) -> None:
        self.tool_calls += 1
        if tool_name in WRITE_TOOLS:
            self.file_writes += 1
        if tool_name in TEST_TOOLS:
            self.test_runs += 1

    def exceeded(self) -> list[str]:
        reasons: list[str] = []
        if self.tool_calls >= self.max_tool_calls:
            reasons.append("max_tool_calls")
        if self.file_writes >= self.max_file_writes:
            reasons.append("max_file_writes")
        if self.test_runs >= self.max_test_runs:
            reasons.append("max_test_runs")
        return reasons

    def exceeded_for(self, tool_name: str) -> list[str]:
        """Return budget limits that should block this specific next tool call."""
        reasons: list[str] = []
        if self.tool_calls >= self.max_tool_calls:
            reasons.append("max_tool_calls")
        if tool_name in WRITE_TOOLS and self.file_writes >= self.max_file_writes:
            reasons.append("max_file_writes")
        if tool_name in TEST_TOOLS and self.test_runs >= self.max_test_runs:
            reasons.append("max_test_runs")
        return reasons

    def to_dict(self) -> dict:
        return {
            "max_tool_calls": self.max_tool_calls,
            "max_file_writes": self.max_file_writes,
            "max_test_runs": self.max_test_runs,
            "tool_calls": self.tool_calls,
            "file_writes": self.file_writes,
            "test_runs": self.test_runs,
        }
