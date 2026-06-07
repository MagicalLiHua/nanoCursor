from __future__ import annotations

import asyncio

from src.api.services import runtime_preparation_service as service


class FakePack:
    relevant_files = ["README.md"]
    recent_changes = ["README.md"]
    file_outlines = [{"path": "README.md"}]

    def to_text(self):
        return "context text"

    def to_dict(self):
        return {"relevant_files": self.relevant_files}

    def estimate_tokens(self):
        return 42


class FakeStore:
    def __init__(self):
        self.updates = []

    def update_session(self, *args, **kwargs):
        self.updates.append((args, kwargs))


def test_prepare_runtime_system_injects_orchestration_and_context(monkeypatch):
    events = []
    activities = []
    store = FakeStore()
    monkeypatch.setattr(service, "_build_core", lambda strategy: f"core:{strategy}")
    monkeypatch.setattr(service, "build_runtime_instructions", lambda plan, team: "runtime instructions")
    monkeypatch.setattr(service, "build_context_pack", lambda **kwargs: FakePack())
    monkeypatch.setattr(service, "save_run_context_pack", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "append_loop_step", lambda *args, **kwargs: None)

    system = service.prepare_runtime_system(
        thread_id="run-1",
        workspace_dir="/tmp/ws",
        messages=[{"role": "user", "content": "inspect"}],
        execution_plan={"strategy": "analysis_only", "stages": []},
        run_team=[],
        conversation_id=None,
        is_lead_direct_run=False,
        uses_runtime_turn_loop=False,
        workdir="/tmp/ws",
        event_store=store,
        emit_event=lambda **kwargs: events.append(kwargs),
        emit_activity=lambda **kwargs: activities.append(kwargs),
    )

    assert "core:analysis_only" in system
    assert "runtime instructions" in system
    assert "context text" in system
    assert store.updates[0][1]["context_pack"]["relevant_files"] == ["README.md"]
    assert [event["event_type"] for event in events] == ["orchestration_applied", "context_pack_built"]
    assert [activity["payload"]["phase"] for activity in activities] == [
        "complexity_assessment",
        "context_pack",
    ]


def test_prepare_runtime_system_degrades_when_context_build_fails(monkeypatch):
    events = []
    monkeypatch.setattr(service, "_build_core", lambda strategy: "core")
    monkeypatch.setattr(service, "build_runtime_instructions", lambda plan, team: "")
    monkeypatch.setattr(service, "build_context_pack", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bad context")))

    system = service.prepare_runtime_system(
        thread_id="run-1",
        workspace_dir="/tmp/ws",
        messages=[],
        execution_plan={},
        run_team=[],
        conversation_id=None,
        is_lead_direct_run=True,
        uses_runtime_turn_loop=True,
        workdir="/tmp/ws",
        event_store=FakeStore(),
        emit_event=lambda **kwargs: events.append(kwargs),
        emit_activity=lambda **kwargs: None,
    )

    assert system.startswith("core")
    assert events[-1]["event_type"] == "context_pack_failed"
    assert events[-1]["content"] == "bad context"


def test_parallel_briefing_is_skipped_for_lightweight_routes():
    calls = []

    async def briefing_runner(**kwargs):
        calls.append(kwargs)
        return {}

    result = asyncio.run(
        service.inject_parallel_briefing(
            thread_id="run-1",
            workspace_dir="/tmp/ws",
            messages=[],
            execution_plan={},
            uses_runtime_turn_loop=True,
            briefing_runner=briefing_runner,
            subagent_runner=lambda *args, **kwargs: None,
            emit_event=lambda **kwargs: None,
            emit_activity=lambda **kwargs: None,
            readonly_tools=[],
        )
    )

    assert result == {}
    assert calls == []


def test_parallel_briefing_is_injected_into_messages():
    messages = [{"role": "user", "content": "build feature"}]
    events = []
    activities = []

    async def briefing_runner(**kwargs):
        return {
            "briefing": "briefing",
            "merge_guidance": "guidance",
            "contributions": {"contributions": [{"name": "Reviewer"}]},
        }

    asyncio.run(
        service.inject_parallel_briefing(
            thread_id="run-1",
            workspace_dir="/tmp/ws",
            messages=messages,
            execution_plan={"strategy": "feature_delivery"},
            uses_runtime_turn_loop=False,
            briefing_runner=briefing_runner,
            subagent_runner=lambda *args, **kwargs: None,
            emit_event=lambda **kwargs: events.append(kwargs),
            emit_activity=lambda **kwargs: activities.append(kwargs),
            readonly_tools=[{"name": "read_file"}],
        )
    )

    assert messages[-1] == {"role": "user", "content": "briefing\n\nguidance"}
    assert events[-1]["event_type"] == "parallel_briefing_injected"
    assert events[-1]["payload"]["contribution_count"] == 1
    assert activities[-1]["payload"]["phase"] == "parallel_briefing_merged"
