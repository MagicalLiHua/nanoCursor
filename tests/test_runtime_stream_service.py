from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.api.services.runtime_stream_service import (
    build_turn_system,
    make_agent_pool_status_callback,
    stream_model_response,
)


class FakeBroker:
    def __init__(self):
        self.published = []

    def publish(self, thread_id, event):
        self.published.append((thread_id, event))


def test_build_turn_system_adds_controller_and_small_edit_constraints():
    system = build_turn_system(
        base_system="base",
        turn_context={"task_summary": "ctx"},
        uses_runtime_turn_loop=True,
        intent_route="small_edit",
        has_tools=True,
    )

    assert "base" in system
    assert "Runtime Step Controller" in system
    assert "受控 small_edit" in system
    assert "<execute>" in system
    assert "ctx" in system


def test_agent_pool_status_callback_emits_unified_event():
    calls = []
    callback = make_agent_pool_status_callback(
        thread_id="run-1",
        workspace_dir="/tmp/ws",
        emit_event=lambda **kwargs: calls.append(kwargs),
    )

    callback(
        SimpleNamespace(
            agent_id="agent-1",
            name="Action Agent",
            role="coder",
            status="completed",
            result="done",
            error=None,
        ),
        "completed",
    )

    assert calls[0]["thread_id"] == "run-1"
    assert calls[0]["workspace_dir"] == "/tmp/ws"
    assert calls[0]["event_type"] == "agent_completed"
    assert calls[0]["agent"] == "action_agent"
    assert calls[0]["payload"]["result"] == "done"


def test_stream_model_response_publishes_tokens_and_metrics():
    broker = FakeBroker()
    metrics = []

    async def fake_stream(**kwargs):
        assert kwargs["session_id"] == "run-1"
        assert kwargs["runtime_context"]["agent"] == "Lead"
        yield ("token", "你")
        yield ("token", "好")
        yield ("metrics", 12, 3)

    result = asyncio.run(
        stream_model_response(
            thread_id="run-1",
            workspace_dir="/tmp/ws",
            messages=[{"role": "user", "content": "hi"}],
            base_system="system",
            tools=[],
            agent_loop_stream=fake_stream,
            token_broker=broker,
            token_counter=7,
            on_llm_response=lambda inp, out: metrics.append((inp, out)),
            runtime_context={"agent": "Lead"},
        )
    )

    assert result.text == "你好"
    assert result.token_counter == 9
    assert [event.content for _, event in broker.published] == ["你", "好"]
    assert broker.published[0][1].id == "run-1-tok-8"
    assert metrics == [(12, 3)]


def test_stream_model_response_raises_stream_error():
    async def fake_stream(**kwargs):
        yield ("error", "boom")

    try:
        asyncio.run(
            stream_model_response(
                thread_id="run-1",
                workspace_dir="/tmp/ws",
                messages=[],
                base_system="system",
                tools=[],
                agent_loop_stream=fake_stream,
                token_broker=FakeBroker(),
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError")
