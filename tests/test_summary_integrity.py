import asyncio
import copy

import pytest

from nanocursor.client import LLMError
from nanocursor.context.manager import CompactCircuitBreaker, CompactEvent, RecoveryState, auto_compact
from nanocursor.conversation import ConversationManager, Message
from nanocursor.memory.session import SessionManager, make_compact_boundary
from nanocursor.tools.base import StreamEnd, TextDelta, ToolCallComplete, ToolCallStart


class SummaryClient:
    def __init__(self, events, *, callback=None, error=None, delay=0):
        self.events = events
        self.callback = callback
        self.error = error
        self.delay = delay
        self.calls = 0
        self.closed = False
        self.histories = []

    async def stream(self, conversation, **kwargs):
        self.calls += 1
        self.histories.append(copy.deepcopy(conversation.history))
        try:
            if self.callback:
                self.callback()
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            for event in self.events:
                yield event
        finally:
            self.closed = True


def long_conversation():
    conversation = ConversationManager()
    for i in range(20):
        conversation.history.append(Message("user" if i % 2 == 0 else "assistant", f"message {i} " + "x" * 12_000))
    conversation.record_usage_anchor(input_tokens=200_000)
    return conversation


@pytest.mark.asyncio
@pytest.mark.parametrize("events", [
    [StreamEnd("end_turn")],
    [TextDelta("  \n"), StreamEnd("end_turn")],
    [TextDelta("<summary></summary>"), StreamEnd("end_turn")],
    [TextDelta("<summary>truncated"), StreamEnd("end_turn")],
    [TextDelta("</summary>reversed<summary>"), StreamEnd("end_turn")],
    [TextDelta("<summary>one</summary><summary>two</summary>"), StreamEnd("end_turn")],
    [TextDelta("<analysis>not closed<summary>claim</summary>"), StreamEnd("end_turn")],
    [TextDelta("<summary>candidate</summary>"), StreamEnd("max_tokens")],
    [TextDelta("<summary>candidate</summary>")],
    [TextDelta("<summary>candidate</summary>"), StreamEnd("end_turn"), StreamEnd("end_turn")],
    [TextDelta("<summary>candidate</summary>"), StreamEnd("end_turn"), TextDelta("late")],
    [ToolCallStart("Bash", "call"), StreamEnd("tool_use")],
    [ToolCallComplete("call", "Bash", {"command": "true"}), StreamEnd("end_turn")],
])
async def test_invalid_summary_preserves_history_usage_and_recovery(tmp_path, events):
    conversation = long_conversation()
    before = copy.deepcopy(conversation.__dict__)
    recovery = RecoveryState()
    recovery.record_file_read("source.py", "retain this context")
    recovery_before = (recovery.snapshot_files(0), recovery.snapshot_skills())
    breaker = CompactCircuitBreaker()
    client = SummaryClient(events)
    result = await auto_compact(conversation, client, 200_000, tmp_path, manual=True, breaker=breaker, recovery=recovery)
    assert isinstance(result, str) and "失败" in result
    assert conversation.__dict__ == before
    assert (recovery.snapshot_files(0), recovery.snapshot_skills()) == recovery_before
    assert breaker.consecutive_failures == 1
    assert client.calls == 1 and client.closed


@pytest.mark.asyncio
async def test_context_overflow_does_not_retry_after_discarding_old_constraints(tmp_path):
    conversation = long_conversation()
    before = copy.deepcopy(conversation.__dict__)
    client = SummaryClient([], error=LLMError("prompt too long"))
    result = await auto_compact(conversation, client, 200_000, tmp_path, manual=True)
    assert isinstance(result, str)
    assert client.calls == 1
    assert conversation.__dict__ == before
    assert conversation.history[0] in client.histories[0]


@pytest.mark.asyncio
async def test_timeout_cancels_summary_and_preserves_history(tmp_path, monkeypatch):
    monkeypatch.setattr("nanocursor.context.manager.SUMMARY_TIMEOUT_SECONDS", 0.01)
    conversation = long_conversation()
    before = copy.deepcopy(conversation.__dict__)
    client = SummaryClient([], delay=30)
    result = await auto_compact(conversation, client, 200_000, tmp_path, manual=True)
    assert isinstance(result, str) and "TimeoutError" in result
    assert client.closed
    assert conversation.__dict__ == before


@pytest.mark.asyncio
async def test_concurrent_history_change_invalidates_summary_without_overwriting_it(tmp_path):
    conversation = long_conversation()
    before = copy.deepcopy(conversation.history)
    new_message = Message("user", "arrived while summarizing")
    client = SummaryClient([TextDelta("<summary>summary</summary>"), StreamEnd("end_turn")],
                           callback=lambda: conversation.history.append(new_message))
    result = await auto_compact(conversation, client, 200_000, tmp_path, manual=True)
    assert isinstance(result, str) and "已改变" in result
    assert conversation.history == before + [new_message]
    assert new_message not in client.histories[0]


@pytest.mark.asyncio
async def test_successful_summary_roundtrips_through_session_boundary(tmp_path):
    conversation = long_conversation()
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    for message in conversation.history:
        session.append(message)
    client = SummaryClient([TextDelta("<analysis>checked</analysis><summary>keep constraints</summary>"), StreamEnd("end_turn")])
    result = await auto_compact(conversation, client, 200_000, tmp_path, manual=True)
    assert isinstance(result, CompactEvent) and result.boundary
    session.append_record(make_compact_boundary(result.boundary.summary, result.boundary.keep))
    session.close()
    restored = manager.resume(session.session_id)
    assert "keep constraints" in restored.messages[0].content
    assert restored.messages[1:] == result.boundary.keep
    assert conversation.baseline_tokens == 0
    restored.session.close()
