from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from src.api.services import runtime_tool_callback_service as service_module
from src.api.services import runtime_approval_wait_service as approval_wait_module
from src.api.services.runtime_tool_callback_service import RuntimeToolCallbacks
from src.runtime.tool_policy_runtime import ToolPolicyDecision


class FakePolicy:
    def __init__(self, decisions):
        self.decisions = decisions
        self.budget = SimpleNamespace(to_dict=lambda: {"tool_calls": 0})
        self.recorded = []

    def check(self, tool_name, tool_input):
        decision = self.decisions[tool_name]
        return ToolPolicyDecision(**decision.to_dict())

    def record(self, tool_name, ok):
        self.recorded.append((tool_name, ok))
        return None


class FakeRun:
    def __init__(self):
        self.metadata = {"lifecycle": {"current_stage_id": "implement"}}
        self.calls = []

    def apply_tool_event(self, **kwargs):
        self.calls.append(kwargs)
        return [{"stage_id": "implement", "status": "completed"}]


class FakeTracker:
    def __init__(self):
        self.changes = []

    def record_change(self, *args):
        self.changes.append(args)


def _callbacks(monkeypatch, policy, *, active_runs=None):
    events = []
    activities = []
    transitions = []
    synced = []
    stages = []
    monkeypatch.setattr(service_module, "check_loop_tool_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(service_module, "record_tool_call_start", lambda **kwargs: SimpleNamespace(call_id="call-1"))
    monkeypatch.setattr(service_module, "record_tool_call_finish", lambda **kwargs: None)
    monkeypatch.setattr(service_module, "append_loop_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(approval_wait_module, "append_loop_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(service_module, "derive_agenthub_events", lambda **kwargs: [])
    callbacks = RuntimeToolCallbacks(
        thread_id="run-1",
        workspace_dir="/tmp/ws",
        policy_runtime=policy,
        change_tracker=FakeTracker(),
        active_runs=active_runs or {},
        runs_lock=threading.RLock(),
        metrics_collector=SimpleNamespace(dump_summary=lambda: {"tools": 1}),
        emit_event=lambda **kwargs: events.append(kwargs),
        emit_activity=lambda **kwargs: activities.append(kwargs),
        transition_state=lambda *args: transitions.append(args),
        sync_run_context=lambda *args: synced.append(args),
        emit_stage_updates=lambda *args: stages.append(args),
        should_cancel=lambda thread_id: False,
        token_metrics=lambda: (12, 4),
    )
    return callbacks, events, activities, transitions, synced, stages


def test_parallel_tool_results_consume_matching_policy_decisions(monkeypatch):
    policy = FakePolicy(
        {
            "read_file": ToolPolicyDecision(tool="read_file", allowed=True, reason="read allowed"),
            "delete_file": ToolPolicyDecision(tool="delete_file", allowed=False, reason="delete blocked"),
        }
    )
    callbacks, events, *_ = _callbacks(monkeypatch, policy)

    asyncio.run(callbacks.on_tool_check("read_file", {"path": "README.md"}))
    asyncio.run(callbacks.on_tool_check("delete_file", {"path": "README.md"}))

    # Complete in the opposite order from the checks.
    callbacks.on_tool_call("delete_file", {"path": "README.md"}, "deleted")
    callbacks.on_tool_call("read_file", {"path": "README.md"}, "contents")

    assert [item["tool"] for item in callbacks.evidence] == ["read_file"]
    assert policy.recorded == [("read_file", True)]
    assert any(event["event_type"] == "tool_policy_blocked" for event in events)


def test_approved_tool_is_auto_allowed_for_later_calls(monkeypatch):
    policy = FakePolicy(
        {
            "delete_file": ToolPolicyDecision(
                tool="delete_file",
                allowed=True,
                requires_approval=True,
                reason="approval required",
            )
        }
    )
    callbacks, events, _, transitions, *_ = _callbacks(monkeypatch, policy)
    monkeypatch.setattr(approval_wait_module, "create_tool_approval", lambda *args, **kwargs: {})

    async def approve(*args, **kwargs):
        return {"status": "approved", "comment": "ok"}

    monkeypatch.setattr(approval_wait_module, "wait_for_approval_async", approve)

    first = asyncio.run(callbacks.on_tool_check("delete_file", {"path": "old.txt"}))
    second = asyncio.run(callbacks.on_tool_check("delete_file", {"path": "other.txt"}))

    assert first.status == "approved"
    assert second.status == "auto_allowed"
    assert second.requires_approval is False
    assert callbacks.approved_tools == {"delete_file"}
    assert transitions[0][2].value == "waiting_approval"
    assert transitions[-1][2].value == "running"
    assert any(event["event_type"] == "approval_resolved" for event in events)


def test_completed_write_records_evidence_change_and_stage(monkeypatch):
    policy = FakePolicy(
        {"write_file": ToolPolicyDecision(tool="write_file", allowed=True, reason="write allowed")}
    )
    run = FakeRun()
    callbacks, events, activities, _, synced, stages = _callbacks(
        monkeypatch,
        policy,
        active_runs={"run-1": run},
    )

    asyncio.run(callbacks.on_tool_check("write_file", {"path": "README.md"}))
    callbacks.on_tool_call("write_file", {"path": "README.md"}, "Updated README.md")

    assert callbacks.evidence[0]["ok"] is True
    assert callbacks.evidence[0]["filetool_evidence"]["backend"] == "python"
    assert callbacks.evidence[0]["filetool_evidence"]["operation"] == "write"
    assert callbacks.change_tracker.changes == [("README.md", "Coder", "modify")]
    assert run.calls[0]["tool_name"] == "write_file"
    assert synced == [("run-1", "/tmp/ws")]
    assert stages[0][2][0]["stage_id"] == "implement"
    assert activities[-1]["input_tokens"] == 12
    tool_event = next(event for event in events if event["event_type"] == "tool_call_finished")
    assert tool_event["payload"]["filetool_evidence"]["path"] == "README.md"


def test_filetool_evidence_emits_backup_and_rollback_events(monkeypatch):
    policy = FakePolicy(
        {
            "edit_file": ToolPolicyDecision(tool="edit_file", allowed=True, reason="edit allowed"),
            "rollback_file": ToolPolicyDecision(tool="rollback_file", allowed=True, reason="rollback allowed"),
        }
    )
    callbacks, events, *_ = _callbacks(monkeypatch, policy)

    asyncio.run(callbacks.on_tool_check("edit_file", {"path": "src/demo.py"}))
    callbacks.on_tool_call(
        "edit_file",
        {"path": "src/demo.py"},
        "成功修改 src/demo.py。使用策略: [行号范围匹配 (Line Range)] (原文件已备份到 src_demo.py.bak.1)\n"
        "Edit Receipt:\n```diff\n-old\n+new\n```",
    )
    assert any(event["event_type"] == "file_backup" for event in events)

    asyncio.run(callbacks.on_tool_check("rollback_file", {"filename": "src/demo.py"}))
    callbacks.on_tool_call(
        "rollback_file",
        {"filename": "src/demo.py"},
        "成功回滚文件 src/demo.py，使用备份: src_demo.py.bak.1",
    )
    rollback_event = next(event for event in events if event["event_type"] == "file_rollback")
    assert rollback_event["payload"]["path"] == "src/demo.py"


def test_filetools_backend_fallback_is_emitted_and_persisted(monkeypatch):
    policy = FakePolicy(
        {"read_file": ToolPolicyDecision(tool="read_file", allowed=True, reason="read allowed")}
    )
    callbacks, events, *_ = _callbacks(monkeypatch, policy)
    monkeypatch.setattr(
        service_module,
        "pop_filetools_backend_event",
        lambda: {
            "backend": "python",
            "fallback": True,
            "from_backend": "go",
            "address": "localhost:50054",
            "reason": "connection refused",
        },
    )

    asyncio.run(callbacks.on_tool_check("read_file", {"path": "README.md"}))
    callbacks.on_tool_call("read_file", {"path": "README.md"}, "README content")

    evidence_event = callbacks.evidence[0]["filetools_backend_event"]
    assert evidence_event["fallback"] is True
    assert evidence_event["from_backend"] == "go"
    tool_event = next(event for event in events if event["event_type"] == "tool_call_finished")
    assert tool_event["payload"]["filetools_backend_event"]["reason"] == "connection refused"
    fallback_event = next(event for event in events if event["event_type"] == "filetools_backend_fallback")
    assert fallback_event["payload"]["tool"] == "read_file"
    assert fallback_event["payload"]["target"] == "README.md"


def test_rejected_risky_file_tool_does_not_record_completed_call(monkeypatch):
    policy = FakePolicy(
        {
            "rollback_file": ToolPolicyDecision(
                tool="rollback_file",
                allowed=True,
                requires_approval=True,
                permission_level="risky_write",
                reason="rollback requires approval",
            )
        }
    )
    callbacks, events, *_ = _callbacks(monkeypatch, policy)
    monkeypatch.setattr(approval_wait_module, "create_tool_approval", lambda *args, **kwargs: {})

    async def reject(*args, **kwargs):
        return {"status": "rejected", "reason": "user rejected"}

    monkeypatch.setattr(approval_wait_module, "wait_for_approval_async", reject)

    decision = asyncio.run(callbacks.on_tool_check("rollback_file", {"filename": "src/app.py"}))
    callbacks.on_tool_call("rollback_file", {"filename": "src/app.py"}, "should not be recorded")

    assert decision.allowed is False
    assert decision.status == "rejected"
    assert callbacks.evidence == []
    assert any(event["event_type"] == "approval_resolved" for event in events)
    assert not any(event["event_type"] == "tool_call_finished" for event in events)
