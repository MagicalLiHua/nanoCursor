"""D1 Runtime Governance tests — ToolPolicy enforcement at runtime."""

import asyncio

import pytest

from src.runtime.run_budget import RunBudget
from src.runtime.tool_policy_runtime import (
    ToolPolicyDecision,
    ToolPolicyRuntime,
    classify_shell_command,
    classify_tool_permission,
)
from src.agent.strategy.tool_policy import policy_for_strategy
from src.agent.engine import _call_tool_check


def test_run_budget_tracks_file_writes():
    budget = RunBudget(max_file_writes=3)
    assert budget.file_writes == 0
    budget.record_tool("write_file")
    assert budget.file_writes == 1
    budget.record_tool("edit_file")
    assert budget.file_writes == 2
    budget.record_tool("read_file")
    assert budget.file_writes == 2  # read_file is not a write tool


def test_run_budget_exceeded_returns_reasons():
    budget = RunBudget(max_tool_calls=3, max_file_writes=1, max_test_runs=1)
    budget.tool_calls = 3
    budget.file_writes = 1
    budget.test_runs = 1
    reasons = budget.exceeded()
    assert "max_tool_calls" in reasons
    assert "max_file_writes" in reasons
    assert "max_test_runs" in reasons


def test_run_budget_not_exceeded():
    budget = RunBudget(max_tool_calls=40)
    budget.tool_calls = 10
    assert budget.exceeded() == []


def test_tool_policy_runtime_allows_declared_tool():
    rt = ToolPolicyRuntime(
        policy={"allowed_tools": ["read_file", "write_file"], "denied_tools": ["delete_file"]},
        budget=RunBudget(max_tool_calls=40),
    )
    d = rt.check("read_file")
    assert d.allowed is True
    assert d.requires_approval is False
    assert d.to_dict()["tool"] == "read_file"


def test_tool_policy_runtime_blocks_denied_tool():
    rt = ToolPolicyRuntime(
        policy={"denied_tools": ["delete_file"]},
        budget=RunBudget(),
    )
    d = rt.check("delete_file")
    assert d.allowed is False


def test_tool_policy_runtime_blocks_not_in_allowlist():
    rt = ToolPolicyRuntime(
        policy={"allowed_tools": ["read_file", "write_file"]},
        budget=RunBudget(),
    )
    d = rt.check("bash")
    assert d.allowed is False


def test_tool_policy_runtime_blocks_budget_exceeded():
    budget = RunBudget(max_tool_calls=3)
    budget.tool_calls = 3  # already at limit
    rt = ToolPolicyRuntime(policy={"allowed_tools": ["read_file"]}, budget=budget)
    d = rt.check("read_file")
    assert d.allowed is False
    assert "max_tool_calls" in d.budget_exceeded


def test_tool_policy_runtime_test_budget_only_blocks_test_tools():
    budget = RunBudget(max_tool_calls=40, max_file_writes=8, max_test_runs=1)
    budget.test_runs = 1
    rt = ToolPolicyRuntime(policy={"allowed_tools": ["read_file", "bash"]}, budget=budget)

    assert rt.check("read_file").allowed is True
    blocked = rt.check("bash")
    assert blocked.allowed is False
    assert blocked.budget_exceeded == ["max_test_runs"]


def test_tool_policy_runtime_requires_approval():
    rt = ToolPolicyRuntime(
        policy={"allowed_tools": ["bash"], "approval_required": ["bash"]},
        budget=RunBudget(),
    )
    d = rt.check("bash")
    assert d.allowed is True
    assert d.requires_approval is True


def test_shell_safe_command_auto_allowed_by_permission_level():
    rt = ToolPolicyRuntime(
        policy={
            "allowed_tools": ["bash"],
            "approval_required_levels": ["risky_write", "shell_risky"],
        },
        budget=RunBudget(),
    )
    d = rt.check("bash", {"command": "pytest tests/test_runtime_governance.py -q"})
    assert d.allowed is True
    assert d.requires_approval is False
    assert d.permission_level == "shell_safe"


def test_shell_risky_command_requires_approval_by_permission_level():
    rt = ToolPolicyRuntime(
        policy={
            "allowed_tools": ["bash"],
            "approval_required_levels": ["risky_write", "shell_risky"],
        },
        budget=RunBudget(),
    )
    d = rt.check("bash", {"command": "pip install requests"})
    assert d.allowed is True
    assert d.requires_approval is True
    assert d.permission_level == "shell_risky"


def test_large_edit_is_risky_write():
    assert classify_tool_permission("edit_file", {"new_text": "x" * 13000}) == "risky_write"


def test_shell_classifier_distinguishes_safe_and_risky():
    assert classify_shell_command("npm run lint") == "shell_safe"
    assert classify_shell_command("python --version 2>&1") == "shell_safe"
    assert classify_shell_command('cd /tmp/workspace && git status 2>&1 || echo "Not a git repo"') == "shell_safe"
    assert classify_shell_command("cd /tmp/workspace && python -m unittest test_sorting.py -v 2>&1") == "shell_safe"
    assert classify_shell_command("python sorting_algorithms.py") == "shell_safe"
    assert classify_shell_command("cd /tmp/workspace && python benchmark.py --quick 2>&1") == "shell_safe"
    assert classify_shell_command("cd /tmp/workspace && timeout 120 python benchmark.py 2>&1") == "shell_safe"
    assert classify_shell_command("cd /tmp/workspace && python sorting_algorithms.py") == "shell_safe"
    assert classify_shell_command("git reset --hard") == "shell_risky"
    assert classify_shell_command("python -c 'import os; os.remove(\"README.md\")'") == "shell_risky"


def test_agent_loop_tool_check_accepts_async_callback():
    async def callback(tool_name: str, tool_input: dict):
        await asyncio.sleep(0)
        return ToolPolicyDecision(tool=tool_name, allowed=False, reason=tool_input["reason"])

    decision = asyncio.run(_call_tool_check(callback, "bash", {"reason": "needs approval"}))
    assert decision.allowed is False
    assert decision.reason == "needs approval"


def test_analysis_only_blocks_write():
    policy = policy_for_strategy("analysis_only")
    rt = ToolPolicyRuntime(policy=policy.to_dict(), budget=RunBudget(max_tool_calls=20))
    assert rt.check("write_file").allowed is False
    assert rt.check("edit_file").allowed is False
    assert rt.check("bash").allowed is False
    assert rt.check("read_file").allowed is True


def test_docs_only_blocks_bash():
    policy = policy_for_strategy("docs_only")
    rt = ToolPolicyRuntime(policy=policy.to_dict(), budget=RunBudget(max_tool_calls=10))
    assert rt.check("bash").allowed is False
    assert rt.check("run_tests").allowed is False
    assert rt.check("write_file").allowed is True  # docs can write
