"""
Tests for src/core/context_manager.py

Covers:
- Token estimation (estimate_token_count, estimate_messages_tokens)
- Dynamic context window sizing (calculate_dynamic_window)
- ContextSlot & priority-based context building
- compress_tool_messages
- extract_original_request, extract_file_signatures
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.core.context_manager import (
    ContextSlot,
    build_context_with_priority,
    build_edit_summary,
    calculate_dynamic_window,
    compress_tool_messages,
    estimate_messages_tokens,
    estimate_token_count,
    extract_file_signatures,
    extract_original_request,
)

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


class TestTokenEstimation:
    def test_empty_string(self):
        assert estimate_token_count("") == 0

    def test_ascii_text(self):
        # Even if tiktoken is not installed, fallback should return > 0
        assert estimate_token_count("hello world") >= 1

    def test_chinese_text(self):
        n = estimate_token_count("你好世界")
        assert n >= 1

    def test_grows_with_length(self):
        short = "a" * 10
        long = "a" * 1000
        assert estimate_token_count(short) < estimate_token_count(long)


class TestMessagesTokenEstimation:
    def test_empty_messages(self):
        assert estimate_messages_tokens([]) == 0

    def test_single_message_has_overhead(self):
        msgs = [HumanMessage(content="hi")]
        n = estimate_messages_tokens(msgs)
        assert n > estimate_token_count("hi")  # has +5 overhead

    def test_tool_calls_add_extra_tokens(self):
        ai = AIMessage(content="")
        ai.tool_calls = [{"args": {"filename": "test.py"}, "name": "read_file"}]
        n_with_tools = estimate_messages_tokens([ai])
        ai_no_tools = AIMessage(content="")
        n_no_tools = estimate_messages_tokens([ai_no_tools])
        # Tool call overhead should make this larger
        assert n_with_tools > n_no_tools


# ---------------------------------------------------------------------------
# Dynamic context window
# ---------------------------------------------------------------------------


class TestDynamicWindow:
    def test_high_usage_shrinks(self):
        # 85% usage should reduce turns
        result = calculate_dynamic_window(
            total_tokens=850, max_tokens=1000, current_turns=4
        )
        assert result < 4

    def test_low_usage_grows(self):
        # 20% usage should increase turns
        result = calculate_dynamic_window(
            total_tokens=50, max_tokens=1000, current_turns=1, min_turns=1, max_turns=4
        )
        assert result > 1

    def test_medium_usage_unchanged(self):
        # 50% usage should keep turns
        result = calculate_dynamic_window(
            total_tokens=500, max_tokens=1000, current_turns=3
        )
        assert result == 3

    def test_respects_min_turns(self):
        result = calculate_dynamic_window(
            total_tokens=990, max_tokens=1000, current_turns=1, min_turns=1, max_turns=8
        )
        assert result >= 1

    def test_respects_max_turns(self):
        result = calculate_dynamic_window(
            total_tokens=50, max_tokens=1000, current_turns=7, min_turns=1, max_turns=8
        )
        assert result <= 8

    def test_zero_max_tokens_guard(self):
        result = calculate_dynamic_window(total_tokens=10, max_tokens=0, current_turns=3)
        # Should treat as 100% usage -> shrink
        assert result <= 3


# ---------------------------------------------------------------------------
# ContextSlot & priority building
# ---------------------------------------------------------------------------


class TestContextSlot:
    def test_token_count_initialized(self):
        msgs = [HumanMessage(content="hello")]
        slot = ContextSlot("test", msgs, priority=1)
        assert slot.token_count > 0
        assert slot.name == "test"

    def test_trim_reduces_token_count(self):
        msgs = [
            HumanMessage(content="msg1"),
            AIMessage(content="msg2"),
            AIMessage(content="msg3"),
        ]
        slot = ContextSlot("test", msgs, priority=2)
        original = slot.token_count
        slot.trim(original - 10)  # trim to less than original
        assert slot.token_count <= original
        assert len(slot.messages) < 3

    def test_fixed_slot_not_trimmable(self):
        msgs = [HumanMessage(content="fixed")]
        slot = ContextSlot("fixed", msgs, priority=1, is_fixed=True)
        slot.trim(0)
        assert len(slot.messages) == 1


class TestBuildContextWithPriority:
    def test_respects_priority_order(self):
        high = [SystemMessage(content="important")]
        low = [HumanMessage(content="details")]
        slots = [
            ContextSlot("low", low, priority=5),
            ContextSlot("high", high, priority=0, is_fixed=True),
        ]
        result = build_context_with_priority(slots, max_tokens=500)
        # High priority (0) should come first
        assert isinstance(result[0], SystemMessage)

    def test_trims_low_priority_when_over_limit(self):
        big = [HumanMessage(content="x" * 1000) for _ in range(5)]
        small = [SystemMessage(content="core")]
        slots = [
            ContextSlot("core", small, priority=0, is_fixed=True),
            ContextSlot("big", big, priority=10),
        ]
        result = build_context_with_priority(slots, max_tokens=200)
        # Core should survive
        assert any(isinstance(m, SystemMessage) and m.content == "core" for m in result)

    def test_empty_slots(self):
        result = build_context_with_priority([], max_tokens=100)
        assert result == []


# ---------------------------------------------------------------------------
# Compression & extraction helpers
# ---------------------------------------------------------------------------


class TestCompressToolMessages:
    def test_short_message_unchanged(self):
        msgs = [ToolMessage(content="short output", name="test_tool", tool_call_id="1")]
        result = compress_tool_messages(msgs)
        assert result[0].content == "short output"

    def test_long_message_truncated(self):
        long_content = "x" * 30000
        msgs = [ToolMessage(content=long_content, name="big_tool", tool_call_id="2")]
        result = compress_tool_messages(msgs, max_content_length=5000)
        output = result[0].content
        assert len(output) < len(long_content)
        assert "中间" in output or "压缩" in output

    def test_non_tool_message_passes_through(self):
        human = HumanMessage(content="test")
        result = compress_tool_messages([human])
        assert result[0] == human


class TestExtractOriginalRequest:
    def test_finds_first_human(self):
        msgs = [
            SystemMessage(content="you are a bot"),
            HumanMessage(content="Build me a calculator"),
            AIMessage(content="Sure!"),
        ]
        assert "calculator" in extract_original_request(msgs)

    def test_fallback_no_human(self):
        msgs = [AIMessage(content="hello"), SystemMessage(content="hi")]
        result = extract_original_request(msgs)
        assert len(result) <= 1000  # Still returns something but truncated


class TestExtractFileSignatures:
    def test_extracts_from_read_file(self):
        msgs = [
            ToolMessage(
                content="--- Content of test.py ---\ndef foo(a, b): pass\nclass Bar(Baz): pass\n",
                name="read_file",
                tool_call_id="1",
            )
        ]
        sigs = extract_file_signatures(msgs)
        assert "test.py" in sigs

    def test_ignores_other_tools(self):
        msgs = [
            ToolMessage(content="done", name="edit_file", tool_call_id="1"),
        ]
        sigs = extract_file_signatures(msgs)
        assert len(sigs) == 0


class TestBuildEditSummary:
    def test_empty_when_no_edits(self):
        msgs = [HumanMessage(content="test")]
        assert build_edit_summary(msgs) == "无文件修改记录。"

    def test_lists_recent_edits(self):
        msgs = [
            ToolMessage(content="Successfully created/updated", name="write_file", tool_call_id="1"),
            ToolMessage(content="成功修改", name="edit_file", tool_call_id="2"),
        ]
        result = build_edit_summary(msgs)
        assert "write_file" in result
        assert "edit_file" in result

    def test_limits_to_max_entries(self):
        msgs = [ToolMessage(content=f"edit {i}", name="edit_file", tool_call_id=str(i)) for i in range(10)]
        result = build_edit_summary(msgs, max_entries=3)
        # Should only show last 3
        assert result.count("edit_file") <= 3
