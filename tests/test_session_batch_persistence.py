"""Partially persisted turn batches retry only their unwritten suffix."""
import pytest

from nanocursor.client import LLMClient
from nanocursor.memory.session import Session
from nanocursor.permissions import PermissionMode
from nanocursor.tools.base import StreamEnd, TextDelta, ToolCallComplete
from test_approval_ui import make_app
from test_auto_approval import setup
from test_status_ui import show_chat


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ["tool_turn", "loop_complete"])
async def test_partial_batch_failure_does_not_repeat_already_saved_messages(setup, monkeypatch, completion):
    class BatchClient(LLMClient):
        def __init__(self):
            self.calls = 0

        async def stream(self, conversation, **kwargs):
            self.calls += 1
            if completion == "tool_turn":
                assert self.calls == 1, "Persistence failure must not replay the tool request"
                yield TextDelta("first saved message")
                yield ToolCallComplete("once", "Bash", {"command": "offline probe"})
                yield StreamEnd("tool_use")
            elif self.calls == 1:
                yield TextDelta("first saved message")
                yield StreamEnd("max_tokens")
            else:
                assert self.calls == 2
                yield TextDelta("final response")
                yield StreamEnd("end_turn")

    app = make_app(setup)
    app.agent.client = BatchClient()
    app.agent.set_permission_mode(PermissionMode.BYPASS)
    original_append = Session.append
    failure_count = 0
    failed_message_attempts = 0

    def append(self, message):
        nonlocal failure_count, failed_message_attempts
        is_second = (bool(message.tool_results) if completion == "tool_turn" else
                     message.content.startswith("Output token limit hit."))
        if self is app.session and is_second:
            failed_message_attempts += 1
            if failure_count == 0:
                failure_count += 1
                raise OSError("second message write temporarily failed")
        return original_append(self, message)

    monkeypatch.setattr(Session, "append", append)
    async with app.run_test() as pilot:
        show_chat(app)
        await app._dispatch_command("run an offline task")
        task = app._agent_task
        assert task is not None
        with pytest.raises(OSError, match="second message write temporarily failed"):
            await task
        assert failure_count == 1 and failed_message_attempts == 2
        restored = app.session_manager.resume(app.session.session_id)
        try:
            assert restored.messages == app.conversation.history
            assert len([message for message in restored.messages if message.content == "first saved message"]) == 1
            if completion == "tool_turn":
                uses = [tool for message in restored.messages for tool in message.tool_uses]
                results = [tool for message in restored.messages for tool in message.tool_results]
                assert len(uses) == len(results) == 1
                assert uses[0].tool_use_id == results[0].tool_use_id == "once"
                assert not results[0].is_error
                assert setup.bash.executed == ["offline probe"]
            else:
                assert len([message for message in restored.messages if message.content.startswith("Output token limit hit.")]) == 1
                assert restored.messages[-1].content == "final response"
        finally:
            restored.session.close()
