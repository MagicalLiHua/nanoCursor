from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from src.api.services.runtime_evidence_service import RuntimeDeliveryEvidence
from src.api.services.runtime_routing_service import (
    execute_lightweight_runtime_route,
    first_action_for_route,
    tools_for_lightweight_route,
    uses_lightweight_runtime,
)


def _turn_runner(actions):
    async def run(thread_id, workspace_dir, *, action, executor=None, **kwargs):
        actions.append(action)
        execution_result = {"executed": False, "result": "not_requested"}
        if executor is not None:
            execution_result = executor(SimpleNamespace(type=action["type"]), {"task_summary": "ctx"})
            if inspect.isawaitable(execution_result):
                execution_result = await execution_result
        return SimpleNamespace(execution_result=execution_result)

    return run


def test_lightweight_route_selection_and_first_actions():
    readonly = [{"name": "read_file"}]
    small_edit = [{"name": "edit_file"}]

    assert uses_lightweight_runtime("direct_answer") is True
    assert uses_lightweight_runtime("feature_delivery") is False
    assert tools_for_lightweight_route(
        "direct_answer", readonly_tools=readonly, small_edit_tools=small_edit
    ) == []
    assert tools_for_lightweight_route(
        "read_only", readonly_tools=readonly, small_edit_tools=small_edit
    ) == readonly
    assert tools_for_lightweight_route(
        "small_edit", readonly_tools=readonly, small_edit_tools=small_edit
    ) == small_edit
    assert first_action_for_route("direct_answer")["type"] == "answer"
    assert first_action_for_route("read_only")["type"] == "inspect_project"


def test_direct_answer_runs_answer_then_finish():
    actions = []
    observed_tools = []

    async def stream_turn(tools, context):
        observed_tools.extend(tools)
        return "hello"

    result = asyncio.run(
        execute_lightweight_runtime_route(
            thread_id="run-1",
            workspace_dir="/tmp/ws",
            intent_route="direct_answer",
            stream_turn=stream_turn,
            readonly_tools=[{"name": "read_file"}],
            small_edit_tools=[{"name": "edit_file"}],
            tool_evidence=[],
            sync_run_context=lambda *args: None,
            turn_runner=_turn_runner(actions),
        )
    )

    assert result == "hello"
    assert observed_tools == []
    assert [action["type"] for action in actions] == ["answer", "finish"]


def test_read_only_runs_inspect_answer_finish():
    actions = []
    observed_tools = []
    readonly_tools = [{"name": "read_file"}]

    async def stream_turn(tools, context):
        observed_tools.extend(tools)
        return "project summary"

    result = asyncio.run(
        execute_lightweight_runtime_route(
            thread_id="run-1",
            workspace_dir="/tmp/ws",
            intent_route="read_only",
            stream_turn=stream_turn,
            readonly_tools=readonly_tools,
            small_edit_tools=[],
            tool_evidence=[],
            sync_run_context=lambda *args: None,
            turn_runner=_turn_runner(actions),
        )
    )

    assert result == "project summary"
    assert observed_tools == readonly_tools
    assert [action["type"] for action in actions] == ["inspect_project", "answer", "finish"]


def test_small_edit_runs_verification_summary_and_finish():
    actions = []
    synced = []
    evidence = RuntimeDeliveryEvidence(
        thread_id="run-1",
        changed_files=[{"path": "README.md"}],
        write_calls=[{"tool": "edit_file", "ok": True}],
        check_calls=[{"tool": "git_diff", "ok": True}],
        ready=True,
        reason="ready",
        diff_source="git",
    )

    result = asyncio.run(
        execute_lightweight_runtime_route(
            thread_id="run-1",
            workspace_dir="/tmp/ws",
            intent_route="small_edit",
            stream_turn=lambda tools, context: _async_value("edited"),
            readonly_tools=[],
            small_edit_tools=[{"name": "edit_file"}],
            tool_evidence=[{"tool": "edit_file", "ok": True}],
            sync_run_context=lambda *args: synced.append(args),
            turn_runner=_turn_runner(actions),
            evidence_collector=lambda *args, **kwargs: evidence,
        )
    )

    assert result == "edited"
    assert synced == [("run-1", "/tmp/ws")]
    assert [action["type"] for action in actions] == [
        "inspect_project",
        "run_checks",
        "summarize",
        "finish",
    ]


def test_small_edit_without_write_degrades_to_summary_and_finish():
    actions = []
    evidence = RuntimeDeliveryEvidence(
        thread_id="run-1",
        ready=False,
        has_write_action=False,
        reason="no successful write",
    )

    result = asyncio.run(
        execute_lightweight_runtime_route(
            thread_id="run-1",
            workspace_dir="/tmp/ws",
            intent_route="small_edit",
            stream_turn=lambda tools, context: _async_value("read-only answer"),
            readonly_tools=[],
            small_edit_tools=[{"name": "edit_file"}],
            tool_evidence=[],
            sync_run_context=lambda *args: None,
            turn_runner=_turn_runner(actions),
            evidence_collector=lambda *args, **kwargs: evidence,
        )
    )

    assert result == "read-only answer"
    assert [action["type"] for action in actions] == ["inspect_project", "summarize", "finish"]
    assert actions[1]["context_requirements"]["degraded_from"] == "small_edit"


def test_small_edit_without_write_and_read_failure_degrades_to_summary_and_finish():
    actions = []
    evidence = RuntimeDeliveryEvidence(
        thread_id="run-1",
        ready=False,
        has_write_action=False,
        failed_calls=[{"tool": "read_file", "ok": False}],
        reason="no successful write",
    )

    result = asyncio.run(
        execute_lightweight_runtime_route(
            thread_id="run-1",
            workspace_dir="/tmp/ws",
            intent_route="small_edit",
            stream_turn=lambda tools, context: _async_value("我查看了一下，目前没有发现需要修改的代码。"),
            readonly_tools=[],
            small_edit_tools=[{"name": "edit_file"}],
            tool_evidence=[],
            sync_run_context=lambda *args: None,
            turn_runner=_turn_runner(actions),
            evidence_collector=lambda *args, **kwargs: evidence,
        )
    )

    assert result == "我查看了一下，目前没有发现需要修改的代码。"
    assert [action["type"] for action in actions] == ["inspect_project", "summarize", "finish"]
    assert actions[1]["context_requirements"]["degraded_from"] == "small_edit"


def test_small_edit_without_write_but_claims_modified_still_fails():
    actions = []
    evidence = RuntimeDeliveryEvidence(
        thread_id="run-1",
        ready=False,
        has_write_action=False,
        reason="no successful write",
    )

    try:
        asyncio.run(
            execute_lightweight_runtime_route(
                thread_id="run-1",
                workspace_dir="/tmp/ws",
                intent_route="small_edit",
                stream_turn=lambda tools, context: _async_value("已修正 README 中的拼写。"),
                readonly_tools=[],
                small_edit_tools=[{"name": "edit_file"}],
                tool_evidence=[],
                sync_run_context=lambda *args: None,
                turn_runner=_turn_runner(actions),
                evidence_collector=lambda *args, **kwargs: evidence,
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "no successful write"
    else:
        raise AssertionError("expected RuntimeError")

    assert [action["type"] for action in actions] == ["inspect_project", "fail"]


def test_small_edit_failed_write_still_fails_before_finish():
    actions = []
    evidence = RuntimeDeliveryEvidence(
        thread_id="run-1",
        ready=False,
        reason="write failed",
        failed_calls=[{"tool": "edit_file", "ok": False}],
    )

    try:
        asyncio.run(
            execute_lightweight_runtime_route(
                thread_id="run-1",
                workspace_dir="/tmp/ws",
                intent_route="small_edit",
                stream_turn=lambda tools, context: _async_value("claimed done"),
                readonly_tools=[],
                small_edit_tools=[{"name": "edit_file"}],
                tool_evidence=[],
                sync_run_context=lambda *args: None,
                turn_runner=_turn_runner(actions),
                evidence_collector=lambda *args, **kwargs: evidence,
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "write failed"
    else:
        raise AssertionError("expected RuntimeError")

    assert [action["type"] for action in actions] == ["inspect_project", "fail"]


async def _async_value(value):
    return value
