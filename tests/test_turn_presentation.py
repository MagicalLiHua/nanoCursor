from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Markdown, Static

from nanocursor.client import LLMClient, LLMError
from nanocursor.tools.base import StreamEnd, TextDelta, ToolCallComplete
from test_agent import MockLLMClient
from test_approval_ui import make_app
from test_auto_approval import setup
from test_status_ui import show_chat


@pytest.mark.asyncio
async def test_streaming_uses_neutral_chinese_and_completion_follows_final_answer(setup):
    entered, release = asyncio.Event(), asyncio.Event()

    class PausedClient(LLMClient):
        async def stream(self, *args, **kwargs):
            entered.set()
            await release.wait()
            yield TextDelta("最终答复")
            yield StreamEnd("end_turn", input_tokens=20, output_tokens=5)

    setup.agent.client = PausedClient()
    app = make_app(setup)
    async with app.run_test(size=(90, 30)) as pilot:
        show_chat(app)
        task = asyncio.create_task(app._send_message("你好"))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await pilot.pause()
            spinner = app.query_one("#spinner-live", Static)
            assert "处理中…" in spinner.render().plain
            app._tick_spinner()
            assert "处理中…" in spinner.render().plain
            assert not app.query(".thinking-done")

            release.set()
            await asyncio.wait_for(task, 3)
            await pilot.pause()
            answer = app.query_one(".ai-message", Markdown)
            completed = app.query_one(".thinking-done", Static)
            assert answer.parent is completed.parent
            siblings = list(answer.parent.children)
            assert siblings.index(answer) < siblings.index(completed)
            assert completed.render().plain.startswith("完成 · ")
            assert completed.render().plain.endswith("s")
            assert not app.query("#spinner-live")
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "iteration_limit"])
async def test_terminal_errors_do_not_show_successful_completion(setup, failure):
    if failure == "provider":
        class FailedClient(LLMClient):
            async def stream(self, *args, **kwargs):
                yield TextDelta("部分答复")
                raise LLMError("synthetic provider failure")
        setup.agent.client = FailedClient()
    else:
        setup.agent.max_iterations = 1
        setup.agent.client = MockLLMClient([[
            ToolCallComplete("unknown", "MissingTool", {}),
            StreamEnd("tool_use", input_tokens=20, output_tokens=5),
        ]])
    app = make_app(setup)
    async with app.run_test(size=(90, 30)) as pilot:
        show_chat(app)
        await app._send_message("测试失败")
        await pilot.pause()
        assert app.query(".error-message")
        assert not app.query(".thinking-done")
        assert not app.query("#spinner-live")
