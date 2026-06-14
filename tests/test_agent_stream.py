"""Tests for agent_loop_stream token streaming."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.engine import _ensure_tool_result_sequence, agent_loop_stream


def test_ensure_tool_result_sequence_repairs_missing_result():
    messages = [
        {"role": "user", "content": "start"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will inspect files."},
                {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {"path": "README.md"}},
            ],
        },
        {"role": "user", "content": "next request"},
    ]

    repaired = _ensure_tool_result_sequence(messages)

    assert repaired[2]["role"] == "user"
    assert repaired[2]["content"][0]["type"] == "tool_result"
    assert repaired[2]["content"][0]["tool_use_id"] == "call-1"
    assert repaired[3]["content"] == "next request"


def test_ensure_tool_result_sequence_orders_partial_results():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call-a", "name": "read_file", "input": {}},
                {"type": "tool_use", "id": "call-b", "name": "list_directory", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "extra note"},
                {"type": "tool_result", "tool_use_id": "call-b", "content": "ok"},
            ],
        },
    ]

    repaired = _ensure_tool_result_sequence(messages)

    content = repaired[1]["content"]
    assert [block["tool_use_id"] for block in content[:2]] == ["call-a", "call-b"]
    assert content[0]["type"] == "tool_result"
    assert content[1]["content"] == "ok"
    assert content[2]["text"] == "extra note"


def _mock_client_factory(responses):
    """Create a mock LLM client that returns streaming responses."""
    call_index = 0

    async def mock_create(**kwargs):
        nonlocal call_index
        if call_index >= len(responses):
            raise StopIteration("No more responses")
        resp = responses[call_index]
        call_index += 1
        return resp

    mock_client = AsyncMock()
    mock_client.messages.create = mock_create
    mock_client.close = AsyncMock()
    return mock_client


class MockStreamEvent:
    def __init__(self, event_type, **kwargs):
        self.type = event_type
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockContentBlock:
    def __init__(self, block_type, text=None, name=None, id=None):
        self.type = block_type
        self.text = text
        self.name = name
        self.id = id


class MockDelta:
    def __init__(self, delta_type, text=None, stop_reason=None, partial_json=None):
        self.type = delta_type
        self.text = text
        self.stop_reason = stop_reason
        self.partial_json = partial_json


class MockUsage:
    def __init__(self, input_tokens=0, output_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class MockMessage:
    def __init__(self, usage=None):
        self.usage = usage or MockUsage()


def test_stream_yields_tokens():
    """agent_loop_stream should yield token events for text content."""

    async def run():
        events = [
            MockStreamEvent("message_start", message=MockMessage(MockUsage(100, 0))),
            MockStreamEvent("content_block_start", content_block=MockContentBlock("text", text="Hello")),
            MockStreamEvent("content_block_delta", delta=MockDelta("text_delta", text=" world")),
            MockStreamEvent("content_block_stop"),
            MockStreamEvent("message_delta", delta=MockDelta("text_delta", stop_reason="end_turn"), usage=MockUsage(0, 10)),
        ]

        async def mock_stream():
            for e in events:
                yield e

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_stream())
        mock_client.close = AsyncMock()

        with patch("src.agent.engine.create_client", return_value=mock_client):
            collected = []
            async for event_type, *data in agent_loop_stream(
                messages=[{"role": "user", "content": "hi"}],
                system="test",
                tools=[],
                max_turns=1,
            ):
                collected.append((event_type, data))

        token_events = [(t, d) for t, d in collected if t == "token"]
        assert len(token_events) == 2
        assert token_events[0][1][0] == "Hello"
        assert token_events[1][1][0] == " world"

        done_events = [(t, d) for t, d in collected if t == "done"]
        assert len(done_events) == 1
        assert done_events[0][1][0] == "Hello world"

    asyncio.run(run())


def test_stream_yields_error_on_failure():
    """agent_loop_stream should yield error event on LLM failure."""

    async def run():
        mock_client = AsyncMock()

        async def mock_create(**kwargs):
            raise RuntimeError("API error")

        mock_client.messages.create = mock_create
        mock_client.close = AsyncMock()

        with patch("src.agent.engine.create_client", return_value=mock_client):
            collected = []
            async for event_type, *data in agent_loop_stream(
                messages=[{"role": "user", "content": "hi"}],
                system="test",
                tools=[],
                max_turns=1,
            ):
                collected.append((event_type, data))

        error_events = [(t, d) for t, d in collected if t == "error"]
        assert len(error_events) == 1
        assert "API error" in error_events[0][1][0]

    asyncio.run(run())


def test_stream_preserves_text_before_tool_use_order():
    """Streaming history must keep text before tool_use so Anthropic accepts the next turn."""

    async def run():
        first_events = [
            MockStreamEvent("message_start", message=MockMessage(MockUsage(100, 0))),
            MockStreamEvent("content_block_start", content_block=MockContentBlock("text", text="I will inspect.")),
            MockStreamEvent("content_block_stop"),
            MockStreamEvent("content_block_start", content_block=MockContentBlock("tool_use", name="list_directory", id="call-1")),
            MockStreamEvent("content_block_delta", delta=MockDelta("input_json_delta", partial_json='{"path":"."}')),
            MockStreamEvent("content_block_stop"),
            MockStreamEvent("message_delta", delta=MockDelta("text_delta", stop_reason="tool_use"), usage=MockUsage(0, 20)),
        ]
        second_events = [
            MockStreamEvent("message_start", message=MockMessage(MockUsage(100, 0))),
            MockStreamEvent("content_block_start", content_block=MockContentBlock("text", text="Done.")),
            MockStreamEvent("content_block_stop"),
            MockStreamEvent("message_delta", delta=MockDelta("text_delta", stop_reason="end_turn"), usage=MockUsage(0, 10)),
        ]

        async def make_stream(events):
            for e in events:
                yield e

        calls = []
        mock_client = AsyncMock()

        async def mock_create(**kwargs):
            calls.append(kwargs)
            return make_stream(first_events if len(calls) == 1 else second_events)

        mock_client.messages.create = mock_create
        mock_client.close = AsyncMock()

        with patch("src.agent.engine.create_client", return_value=mock_client):
            collected = []
            async for event_type, *data in agent_loop_stream(
                messages=[{"role": "user", "content": "list files"}],
                system="test",
                tools=[],
                max_turns=2,
            ):
                collected.append((event_type, data))

        second_messages = calls[1]["messages"]
        assistant = second_messages[1]
        assert [block["type"] for block in assistant["content"]] == ["text", "tool_use"]
        assert second_messages[2]["content"][0]["tool_use_id"] == "call-1"
        assert any(event_type == "done" for event_type, _ in collected)

    asyncio.run(run())


def test_stream_repairs_text_execute_tags_into_tool_calls(monkeypatch):
    """Text-only command tags should become governed tool calls, not final prose."""

    async def run():
        first_events = [
            MockStreamEvent("message_start", message=MockMessage(MockUsage(100, 0))),
            MockStreamEvent("content_block_start", content_block=MockContentBlock("text", text="我先检查目录。\n<execute><cmd>ls -la .</cmd></execute>")),
            MockStreamEvent("content_block_stop"),
            MockStreamEvent("message_delta", delta=MockDelta("text_delta", stop_reason="end_turn"), usage=MockUsage(0, 20)),
        ]
        second_events = [
            MockStreamEvent("message_start", message=MockMessage(MockUsage(100, 0))),
            MockStreamEvent("content_block_start", content_block=MockContentBlock("text", text="目录检查完成。")),
            MockStreamEvent("content_block_stop"),
            MockStreamEvent("message_delta", delta=MockDelta("text_delta", stop_reason="end_turn"), usage=MockUsage(0, 10)),
        ]

        async def make_stream(events):
            for event in events:
                yield event

        calls = []
        mock_client = AsyncMock()

        async def mock_create(**kwargs):
            calls.append(kwargs)
            return make_stream(first_events if len(calls) == 1 else second_events)

        mock_client.messages.create = mock_create
        mock_client.close = AsyncMock()
        monkeypatch.setitem(
            __import__("src.agent.engine", fromlist=["TOOL_HANDLERS"]).TOOL_HANDLERS,
            "bash",
            lambda command: f"ran: {command}",
        )

        with patch("src.agent.engine.create_client", return_value=mock_client):
            collected = []
            async for event_type, *data in agent_loop_stream(
                messages=[{"role": "user", "content": "inspect"}],
                system="test",
                tools=[{"name": "bash"}],
                max_turns=2,
            ):
                collected.append((event_type, data))

        assert ("tool_start", ["bash"]) in collected
        assert any(event_type == "tool_result" and data[0] == "bash" and "ran: ls -la ." in data[2] for event_type, data in collected)
        assert any(event_type == "done" and data[0] == "目录检查完成。" for event_type, data in collected)

        second_messages = calls[1]["messages"]
        assistant = second_messages[1]
        assert [block["type"] for block in assistant["content"]] == ["text", "tool_use"]
        assert assistant["content"][0]["text"] == "我先检查目录。"
        assert assistant["content"][1]["name"] == "bash"
        assert second_messages[2]["content"][0]["content"] == "ran: ls -la ."

    asyncio.run(run())
