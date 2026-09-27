import pytest

from nanocursor.context.manager import (
    CompactEvent, auto_compact, ensure_session_dir, make_persisted_preview, persist_tool_result,
)
from nanocursor.conversation import ConversationManager, ToolResultBlock, ToolUseBlock
from nanocursor.tools.base import StreamEnd, TextDelta


class SummaryClient:
    async def stream(self, *args, **kwargs):
        yield TextDelta("<summary>A summary of the old conversation</summary>")
        yield StreamEnd("end_turn")


@pytest.mark.asyncio
async def test_compact_preserves_retained_and_other_session_output(tmp_path):
    directory = ensure_session_dir(str(tmp_path))
    content = "x" * 60000
    output = persist_tool_result("recent-output", content, directory)
    other = persist_tool_result("other-session", "another agent's output", directory)
    conv = ConversationManager()
    for _ in range(10):
        conv.add_user_message("old context " * 1500)
        conv.add_assistant_message("old reply")
    conv.add_user_message("recent task")
    conv.add_assistant_message("", [ToolUseBlock("recent-output", "Bash", {"command": "synthetic"})])
    conv.add_tool_results_message([ToolResultBlock("recent-output", make_persisted_preview(content, output))])
    conv.add_assistant_message("received output")
    conv.add_user_message("continue")
    result = await auto_compact(conv, SummaryClient(), 128000, directory, manual=True)
    assert isinstance(result, CompactEvent)
    assert any(str(output) in tr.content for msg in conv.history for tr in msg.tool_results)
    assert output.read_text() == content
    assert other.read_text() == "another agent's output"
