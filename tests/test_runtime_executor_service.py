"""Focused tests for the core runtime executor service."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from src.agent.state import WorkflowCancelledError
from src.api.services import runtime_executor_service as service


class DummyRunManager:
    def __init__(self):
        self.unregistered: list[str] = []

    def get_state_machine(self, thread_id: str):
        return None

    def unregister(self, thread_id: str):
        self.unregistered.append(thread_id)


class DummyMetrics:
    def __init__(self):
        self.flushed = False
        self.history_tags: list[str] = []

    def flush_to_file(self):
        self.flushed = True

    def append_to_history(self, path: str, tag: str):
        self.history_tags.append(tag)


def _deps(tmp_path, active_runs: dict | None = None):
    events: list[dict] = []
    activities: list[dict] = []
    stage_updates: list[dict] = []
    syncs: list[dict] = []
    cleanups: list[str] = []
    metrics = DummyMetrics()
    run_manager = DummyRunManager()

    deps = service.RuntimeExecutorDependencies(
        agent_loop_stream=lambda **kwargs: None,
        run_subagent=lambda **kwargs: "readonly",
        tools=[{"name": "read_file"}, {"name": "write_file"}, {"name": "run_tests"}],
        get_workdir=lambda: tmp_path,
        get_file_lock=lambda thread_id: SimpleNamespace(thread_id=thread_id),
        cleanup_file_lock=lambda thread_id: cleanups.append(thread_id),
        metrics_collector=metrics,
        metrics_history_file=str(tmp_path / "metrics.json"),
        run_parallel_agent_briefing=lambda **kwargs: {"enabled": False, "briefing": ""},
        emit_event=lambda **kwargs: events.append(kwargs),
        emit_activity=lambda **kwargs: activities.append(kwargs),
        emit_stage_updates=lambda **kwargs: stage_updates.append(kwargs),
        transition_state=lambda **kwargs: None,
        sync_run_context=lambda **kwargs: syncs.append(kwargs),
        get_workspace=lambda: str(tmp_path),
        run_manager=run_manager,
        active_runs=active_runs if active_runs is not None else {},
        runs_lock=threading.RLock(),
        event_store=SimpleNamespace(),
    )
    return deps, events, activities, stage_updates, syncs, cleanups, metrics, run_manager


def test_standard_workflow_completed_finalizes_run(tmp_path, monkeypatch):
    thread_id = "run-completed"
    active_runs = {
        thread_id: {
            "workspace_dir": str(tmp_path),
            "execution_plan": {"intent_decision": {"route": "implementation"}},
            "team": [],
            "conversation_id": "conv-1",
        }
    }
    deps, events, *_ = _deps(tmp_path, active_runs)
    calls: list[tuple[str, dict]] = []

    async def fake_stream_model_response(**kwargs):
        kwargs["on_llm_response"](11, 7)
        return SimpleNamespace(text="交付完成", token_counter=2)

    async def fake_inject_parallel_briefing(**kwargs):
        return {}

    monkeypatch.setattr(service, "prepare_runtime_system", lambda **kwargs: "system")
    monkeypatch.setattr(service, "inject_parallel_briefing", fake_inject_parallel_briefing)
    monkeypatch.setattr(service, "stream_model_response", fake_stream_model_response)
    monkeypatch.setattr(service, "complete_workflow_run", lambda **kwargs: calls.append(("complete", kwargs)))
    monkeypatch.setattr(service, "finalize_delivery_best_effort", lambda *args, **kwargs: calls.append(("delivery", {"args": args})))
    monkeypatch.setattr(service, "finalize_run_registry", lambda **kwargs: calls.append(("registry", kwargs)))
    monkeypatch.setattr(service, "_cleanup_agent_pool", lambda thread_id: calls.append(("pool_cleanup", {"thread_id": thread_id})))

    asyncio.run(
        service.run_workflow_async(
            thread_id,
            [SimpleNamespace(type="user", content="实现功能")],
            max_retries=3,
            max_coder_steps=15,
            workspace_dir=str(tmp_path),
            dependencies=deps,
        )
    )

    assert any(call[0] == "complete" for call in calls)
    assert any(call[0] == "delivery" for call in calls)
    assert any(call[0] == "pool_cleanup" for call in calls)
    assert [call[1]["final_status"] for call in calls if call[0] == "registry"] == ["completed"]
    assert any(event["event_type"] == "metrics_updated" for event in events)


def test_standard_write_workflow_rejects_completion_without_changes(tmp_path, monkeypatch):
    thread_id = "run-no-write-evidence"
    active_runs = {
        thread_id: {
            "workspace_dir": str(tmp_path),
            "execution_plan": {
                "intent_decision": {
                    "route": "feature_delivery",
                    "execution_route": "agenthub_delivery",
                    "requires_workspace_write": True,
                }
            },
            "team": [],
            "conversation_id": "conv-1",
        }
    }
    deps, *_ = _deps(tmp_path, active_runs)
    calls: list[tuple[str, dict]] = []

    async def fake_stream_model_response(**kwargs):
        return SimpleNamespace(text="已完成。", token_counter=1)

    async def fake_inject_parallel_briefing(**kwargs):
        return {}

    monkeypatch.setattr(service, "prepare_runtime_system", lambda **kwargs: "system")
    monkeypatch.setattr(service, "inject_parallel_briefing", fake_inject_parallel_briefing)
    monkeypatch.setattr(service, "stream_model_response", fake_stream_model_response)
    monkeypatch.setattr(
        service,
        "collect_runtime_delivery_evidence",
        lambda *args, **kwargs: SimpleNamespace(has_changes=False),
    )
    monkeypatch.setattr(service, "complete_workflow_run", lambda **kwargs: calls.append(("complete", kwargs)))
    monkeypatch.setattr(service, "fail_workflow_run", lambda **kwargs: calls.append(("fail", kwargs)))
    monkeypatch.setattr(service, "finalize_delivery_best_effort", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "finalize_run_registry", lambda **kwargs: calls.append(("registry", kwargs)))
    monkeypatch.setattr(service, "_cleanup_agent_pool", lambda thread_id: None)

    asyncio.run(
        service.run_workflow_async(
            thread_id,
            [SimpleNamespace(type="user", content="创建一个课程设计项目")],
            max_retries=3,
            max_coder_steps=15,
            workspace_dir=str(tmp_path),
            dependencies=deps,
        )
    )

    assert not any(call[0] == "complete" for call in calls)
    assert any(
        call[0] == "fail"
        and "未检测到真实文件变更" in call[1]["error_detail"]
        for call in calls
    )
    assert [call[1]["final_status"] for call in calls if call[0] == "registry"] == ["failed"]


def test_standard_workflow_parallel_subagent_runner_accepts_positional_prompt(tmp_path, monkeypatch):
    thread_id = "run-parallel-runner-bridge"
    active_runs = {
        thread_id: {
            "workspace_dir": str(tmp_path),
            "execution_plan": {
                "intent_decision": {"route": "implementation"},
                "strategy": "feature_delivery",
                "stages": [{"id": "plan"}, {"id": "implement"}],
            },
            "team": [],
            "conversation_id": "conv-1",
        }
    }
    deps, *_ = _deps(tmp_path, active_runs)
    calls: list[tuple[str, dict]] = []
    seen: dict[str, object] = {}

    async def fake_run_subagent(**kwargs):
        seen["subagent_kwargs"] = kwargs
        return "readonly summary"

    async def fake_inject_parallel_briefing(**kwargs):
        seen["runner_result"] = await kwargs["subagent_runner"](
            "positional prompt",
            system="worker system",
            agent_type="tester",
            tools=[],
        )
        return {}

    async def fake_stream_model_response(**kwargs):
        return SimpleNamespace(text="交付完成", token_counter=1)

    deps.run_subagent = fake_run_subagent
    monkeypatch.setattr(service, "prepare_runtime_system", lambda **kwargs: "system")
    monkeypatch.setattr(service, "inject_parallel_briefing", fake_inject_parallel_briefing)
    monkeypatch.setattr(service, "stream_model_response", fake_stream_model_response)
    monkeypatch.setattr(service, "complete_workflow_run", lambda **kwargs: calls.append(("complete", kwargs)))
    monkeypatch.setattr(service, "finalize_delivery_best_effort", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "finalize_run_registry", lambda **kwargs: None)
    monkeypatch.setattr(service, "_cleanup_agent_pool", lambda thread_id: None)

    asyncio.run(
        service.run_workflow_async(
            thread_id,
            [SimpleNamespace(type="user", content="实现功能")],
            max_retries=3,
            max_coder_steps=15,
            workspace_dir=str(tmp_path),
            dependencies=deps,
        )
    )

    assert seen["runner_result"] == "readonly summary"
    assert seen["subagent_kwargs"]["prompt"] == "positional prompt"
    assert seen["subagent_kwargs"]["system"] == "worker system"
    assert seen["subagent_kwargs"]["agent_type"] == "tester"
    assert [tool["name"] for tool in seen["subagent_kwargs"]["tools"]] == ["read_file"]
    assert any(call[0] == "complete" for call in calls)


def test_standard_workflow_cancelled_finalizes_cancel_path(tmp_path, monkeypatch):
    thread_id = "run-cancelled"
    active_runs = {
        thread_id: {
            "workspace_dir": str(tmp_path),
            "status": "cancelling",
            "execution_plan": {},
            "team": [],
        }
    }
    deps, *_ = _deps(tmp_path, active_runs)
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(service, "cancel_workflow_run", lambda **kwargs: calls.append(("cancel", kwargs)))
    monkeypatch.setattr(service, "finalize_delivery_best_effort", lambda *args, **kwargs: calls.append(("delivery", {"args": args})))
    monkeypatch.setattr(service, "finalize_run_registry", lambda **kwargs: calls.append(("registry", kwargs)))
    monkeypatch.setattr(service, "_cleanup_agent_pool", lambda thread_id: None)

    asyncio.run(
        service.run_workflow_async(
            thread_id,
            [SimpleNamespace(type="user", content="停止")],
            max_retries=3,
            max_coder_steps=15,
            workspace_dir=str(tmp_path),
            dependencies=deps,
        )
    )

    assert any(call[0] == "cancel" for call in calls)
    assert [call[1]["final_status"] for call in calls if call[0] == "registry"] == ["cancelled"]


def test_standard_workflow_failed_finalizes_failure_path(tmp_path, monkeypatch):
    thread_id = "run-failed"
    active_runs = {
        thread_id: {
            "workspace_dir": str(tmp_path),
            "execution_plan": {},
            "team": [],
        }
    }
    deps, *_ = _deps(tmp_path, active_runs)
    calls: list[tuple[str, dict]] = []

    async def fake_stream_model_response(**kwargs):
        raise RuntimeError("model exploded")

    async def fake_inject_parallel_briefing(**kwargs):
        return {}

    monkeypatch.setattr(service, "prepare_runtime_system", lambda **kwargs: "system")
    monkeypatch.setattr(service, "inject_parallel_briefing", fake_inject_parallel_briefing)
    monkeypatch.setattr(service, "stream_model_response", fake_stream_model_response)
    monkeypatch.setattr(service, "fail_workflow_run", lambda **kwargs: calls.append(("fail", kwargs)))
    monkeypatch.setattr(service, "finalize_delivery_best_effort", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "finalize_run_registry", lambda **kwargs: calls.append(("registry", kwargs)))
    monkeypatch.setattr(service, "_cleanup_agent_pool", lambda thread_id: None)

    asyncio.run(
        service.run_workflow_async(
            thread_id,
            [SimpleNamespace(type="user", content="执行")],
            max_retries=3,
            max_coder_steps=15,
            workspace_dir=str(tmp_path),
            dependencies=deps,
        )
    )

    assert any(call[0] == "fail" and "model exploded" in call[1]["error_detail"] for call in calls)
    assert [call[1]["final_status"] for call in calls if call[0] == "registry"] == ["failed"]


def test_resume_workflow_completed_persists_terminal_session(tmp_path, monkeypatch):
    thread_id = "resume-completed"
    deps, events, *_ = _deps(tmp_path)
    calls: list[tuple[str, dict]] = []

    async def fake_stream_model_response(**kwargs):
        kwargs["on_llm_response"](3, 5)
        return SimpleNamespace(text="继续完成", token_counter=4)

    monkeypatch.setattr(service, "stream_model_response", fake_stream_model_response)
    monkeypatch.setattr(service, "persist_terminal_session", lambda **kwargs: calls.append(("session", kwargs)))
    monkeypatch.setattr(service, "finalize_run_registry", lambda **kwargs: calls.append(("registry", kwargs)))

    asyncio.run(
        service.run_workflow_async_from_messages(
            thread_id,
            [{"role": "user", "content": "继续"}],
            "system",
            str(tmp_path),
            dependencies=deps,
        )
    )

    assert [call[1]["status"] for call in calls if call[0] == "session"] == ["completed"]
    assert [call[1]["final_status"] for call in calls if call[0] == "registry"] == ["completed"]
    assert [event["event_type"] for event in events] == ["metrics_updated", "assistant_message", "done"]


def test_resume_workflow_cancelled_persists_cancelled_session(tmp_path, monkeypatch):
    thread_id = "resume-cancelled"
    deps, events, *_ = _deps(tmp_path)
    calls: list[tuple[str, dict]] = []

    async def fake_stream_model_response(**kwargs):
        raise WorkflowCancelledError("cancelled")

    monkeypatch.setattr(service, "stream_model_response", fake_stream_model_response)
    monkeypatch.setattr(service, "persist_terminal_session", lambda **kwargs: calls.append(("session", kwargs)))
    monkeypatch.setattr(service, "finalize_run_registry", lambda **kwargs: calls.append(("registry", kwargs)))

    asyncio.run(
        service.run_workflow_async_from_messages(
            thread_id,
            [{"role": "user", "content": "继续"}],
            "system",
            str(tmp_path),
            dependencies=deps,
        )
    )

    assert [call[1]["status"] for call in calls if call[0] == "session"] == ["cancelled"]
    assert [call[1]["final_status"] for call in calls if call[0] == "registry"] == ["cancelled"]
    assert [event["event_type"] for event in events] == ["done"]


def test_resume_workflow_failed_persists_failed_session(tmp_path, monkeypatch):
    thread_id = "resume-failed"
    deps, events, *_ = _deps(tmp_path)
    calls: list[tuple[str, dict]] = []

    async def fake_stream_model_response(**kwargs):
        raise RuntimeError("resume exploded")

    monkeypatch.setattr(service, "stream_model_response", fake_stream_model_response)
    monkeypatch.setattr(service, "persist_terminal_session", lambda **kwargs: calls.append(("session", kwargs)))
    monkeypatch.setattr(service, "finalize_run_registry", lambda **kwargs: calls.append(("registry", kwargs)))

    asyncio.run(
        service.run_workflow_async_from_messages(
            thread_id,
            [{"role": "user", "content": "继续"}],
            "system",
            str(tmp_path),
            dependencies=deps,
        )
    )

    assert [call[1]["status"] for call in calls if call[0] == "session"] == ["failed"]
    assert [call[1]["final_status"] for call in calls if call[0] == "registry"] == ["failed"]
    assert [event["event_type"] for event in events] == ["error"]
