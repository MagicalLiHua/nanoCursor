"""Missing API usage remains distinct from a measured zero in the TUI."""
from types import SimpleNamespace as NS

import pytest

from nanocursor.agent import Agent
from nanocursor.commands.handlers.clear import handle_clear
from nanocursor.commands.registry import CommandContext
from nanocursor.conversation import ConversationManager
from nanocursor.status import StatusSnapshot, collect_status, format_status_details
from nanocursor.status_bar import status_lines
from nanocursor.tools import ToolRegistry
from test_stream_usage import chunk, client_for, usage


@pytest.mark.asyncio
async def test_missing_then_reported_usage_and_clear(tmp_path):
    agent = Agent(client_for([chunk(content="Hello", finish="stop")]), ToolRegistry(),
                  protocol="openai-compat", work_dir=str(tmp_path),
                  inject_environment_context=False)
    app = NS(agent=agent, conversation=ConversationManager(),
             refresh_status=lambda: None, add_system_message=lambda text: None)
    app.conversation.add_user_message("Hello")
    async for _ in agent.run(app.conversation):
        pass
    missing = collect_status(app)
    assert missing.usage_missing_requests == 1
    assert "Token —" in status_lines(missing, 90)[1].plain
    assert "服务未返回用量" in format_status_details(missing)
    assert "输入 0" not in format_status_details(missing)

    agent.client = client_for([chunk(content="Another answer", finish="stop", usage=usage(1040, 50))])
    app.conversation.add_user_message("Continue")
    async for _ in agent.run(app.conversation):
        pass
    partial = collect_status(app)
    assert (partial.input_tokens, partial.output_tokens) == (1040, 50)
    assert partial.usage_missing_requests == 1
    assert "↑50*" in status_lines(partial, 90)[1].plain
    assert "仅已上报部分" in format_status_details(partial)

    ctx = CommandContext("", agent, app.conversation, None, None, None, app, {
        "set_conversation": lambda conv: setattr(app, "conversation", conv),
        "clear_chat": lambda: None,
    })
    await handle_clear(ctx)
    cleared = collect_status(app)
    assert (cleared.input_tokens, cleared.output_tokens, cleared.usage_missing_requests) == (0, 0, 0)
    assert "Token ↓0 ↑0" in status_lines(cleared, 90)[1].plain


@pytest.mark.parametrize("used,percentage", [(0, "0%"), (1, "<1%"), (1200, "<1%"), (1280, "1%")])
def test_small_positive_context_is_not_displayed_as_zero(used, percentage):
    snapshot = StatusSnapshot(model="deepseek-v4-flash", context_used=used, context_window=128_000)
    first, _ = status_lines(snapshot, 90)
    assert first.plain.endswith(percentage)
    assert "/128k" in first.plain
