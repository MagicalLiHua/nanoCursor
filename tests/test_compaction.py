"""Tests for src/agent/compaction.py"""
from __future__ import annotations

from src.agent.compaction import (
    COMPACT_TOKEN_THRESHOLD,
    MAX_RECENT_TOOL_RESULTS,
    _content_to_dict,
    auto_compact,
    micro_compact,
)


# --- _content_to_dict ---


def test_content_to_dict_text_block():
    class Block:
        type = "text"
        text = "hello"

    result = _content_to_dict(Block())
    assert result == {"type": "text", "text": "hello"}


def test_content_to_dict_tool_use_block():
    class Block:
        type = "tool_use"
        id = "tu_123"
        name = "bash"
        input = {"cmd": "ls"}

    result = _content_to_dict(Block())
    assert result["type"] == "tool_use"
    assert result["id"] == "tu_123"
    assert result["name"] == "bash"


def test_content_to_dict_tool_result_block():
    class Block:
        type = "tool_result"
        tool_use_id = "tu_123"
        content = "output"

    result = _content_to_dict(Block())
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "tu_123"


def test_content_to_dict_thinking_block():
    class Block:
        type = "thinking"
        thinking = "reasoning"
        signature = "sig"

    result = _content_to_dict(Block())
    assert result["type"] == "thinking"
    assert result["thinking"] == "reasoning"


def test_content_to_dict_unknown_type():
    class Block:
        type = "custom"

    result = _content_to_dict(Block())
    assert result == {"type": "custom"}


def test_content_to_dict_list():
    class Block:
        type = "text"
        text = "hi"

    result = _content_to_dict([Block()])
    assert isinstance(result, list)
    assert result[0]["text"] == "hi"


def test_content_to_dict_plain_value():
    assert _content_to_dict("plain") == "plain"
    assert _content_to_dict(42) == 42


# --- micro_compact ---


def test_micro_compact_preserves_recent_tool_results():
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "1", "content": "result 1"},
            {"type": "tool_result", "tool_use_id": "2", "content": "result 2"},
            {"type": "tool_result", "tool_use_id": "3", "content": "result 3"},
            {"type": "tool_result", "tool_use_id": "4", "content": "result 4"},
        ]},
    ]

    result = micro_compact(messages)
    content = result[0]["content"]

    # First 3 kept as-is
    assert content[0]["content"] == "result 1"
    assert content[1]["content"] == "result 2"
    assert content[2]["content"] == "result 3"
    # 4th summarized
    assert "chars tool output" in content[3]["content"]


def test_micro_compact_preserves_non_tool_messages():
    messages = [
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "hi"},
    ]
    result = micro_compact(messages)
    assert len(result) == 2
    assert result[0]["role"] == "assistant"


def test_micro_compact_handles_object_messages():
    class Msg:
        role = "user"
        content = [
            {"type": "tool_result", "tool_use_id": "1", "content": "a" * 100},
            {"type": "tool_result", "tool_use_id": "2", "content": "b" * 100},
            {"type": "tool_result", "tool_use_id": "3", "content": "c" * 100},
            {"type": "tool_result", "tool_use_id": "4", "content": "d" * 100},
        ]

    result = micro_compact([Msg()])
    content = result[0]["content"]
    assert len(content) == 4
    # 4th should be summarized
    assert "chars tool output" in content[3]["content"]


# --- auto_compact ---


def test_auto_compact_returns_unchanged_when_small():
    messages = [{"role": "user", "content": "short"}]
    result = auto_compact(messages)
    assert result == messages


def test_auto_compact_triggers_micro_compact_when_large():
    # Create messages that exceed COMPACT_TOKEN_THRESHOLD
    large_content = "x" * 60000
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "1", "content": large_content},
            {"type": "tool_result", "tool_use_id": "2", "content": large_content},
            {"type": "tool_result", "tool_use_id": "3", "content": large_content},
            {"type": "tool_result", "tool_use_id": "4", "content": large_content},
        ]},
    ]

    result = auto_compact(messages)
    content = result[0]["content"]
    # 4th tool result should be summarized
    assert "chars tool output" in content[3]["content"]
