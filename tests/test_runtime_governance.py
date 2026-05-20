"""D1 Runtime Governance tests — ToolPolicy enforcement at runtime."""

import asyncio

import pytest

from src.runtime.run_budget import RunBudget
from src.runtime.tool_policy_runtime import ToolPolicyDecision, ToolPolicyRuntime
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


def test_tool_policy_runtime_requires_approval():
    rt = ToolPolicyRuntime(
        policy={"allowed_tools": ["bash"], "approval_required": ["bash"]},
        budget=RunBudget(),
    )
    d = rt.check("bash")
    assert d.allowed is True
    assert d.requires_approval is True


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
