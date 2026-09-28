from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from nanocursor.client import LLMError, OpenAIClient, OpenAICompatClient, collect_text_response
from nanocursor.conversation import ConversationManager
from nanocursor.tools.base import StreamEnd, ToolCallComplete


async def events(items):
    for item in items:
        yield item


def responses_client(items):
    client = OpenAIClient.__new__(OpenAIClient)
    client.model = "offline"
    client.max_output_tokens = 1234
    client._request_options = {}
    client._client = NS(responses=NS(create=AsyncMock(return_value=events(items))))
    return client


def terminal(kind="completed", *, reason=None, usage=None):
    return NS(type=f"response.{kind}", response=NS(
        incomplete_details=NS(reason=reason), usage=usage,
        error=NS(message="model server failed"),
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("tail,expected", [
    ([], "without a terminal"),
    ([terminal("failed")], "model server failed"),
    ([NS(type="error", message="stream error", code="bad")], "stream error"),
    ([terminal("incomplete", reason="content_filter")], "content_filter"),
    ([terminal("incomplete")], "unknown reason"),
    ([terminal(), terminal()], "after its terminal"),
    ([terminal(), NS(type="response.output_text.delta", delta="late")], "after its terminal"),
])
async def test_responses_abnormal_termination_never_yields_normal_end(tail, expected):
    client = responses_client([NS(type="response.output_text.delta", delta="partial"), *tail])
    observed = []
    with pytest.raises(LLMError, match=expected):
        async for event in client.stream(ConversationManager()):
            observed.append(event)
    assert not any(isinstance(event, StreamEnd) for event in observed)


@pytest.mark.asyncio
async def test_responses_output_cap_and_incomplete_usage_are_preserved():
    client = responses_client([terminal("incomplete", reason="max_output_tokens", usage=NS(
        input_tokens=100, output_tokens=1234, input_tokens_details=NS(cached_tokens=30),
    ))])
    observed = [event async for event in client.stream(ConversationManager())]
    assert observed == [StreamEnd("max_tokens", 70, 1234, 30)]
    assert client._client.responses.create.call_args.kwargs["max_output_tokens"] == 1234


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected", [(400, 400), (9999, 1234)])
async def test_text_collection_caps_one_request_without_mutating_main_client(override, expected):
    client = responses_client([NS(type="response.output_text.delta", delta="valid"), terminal()])
    result = await collect_text_response(client, ConversationManager(), max_output_tokens=override,
                                         tools=[{"type": "function", "name": "example", "parameters": {}}])
    assert result.text == "valid" and not result.end.usage_available
    sent = client._client.responses.create.call_args.kwargs
    assert sent["max_output_tokens"] == expected
    assert sent["tool_choice"] == "none"
    assert client.max_output_tokens == 1234


@pytest.mark.asyncio
async def test_partial_tool_arguments_are_never_dispatched_on_output_limit():
    items = [NS(type="response.output_item.added", output_index=0,
                item=NS(type="function_call", id="item", call_id="call", name="WriteFile")),
             NS(type="response.function_call_arguments.delta", item_id="item", output_index=0, delta='{"file_path":'),
             terminal("incomplete", reason="max_output_tokens")]
    observed = []
    with pytest.raises(LLMError, match="before tool arguments completed"):
        async for event in responses_client(items).stream(ConversationManager()):
            observed.append(event)
    assert not any(isinstance(event, (ToolCallComplete, StreamEnd)) for event in observed)


@pytest.mark.asyncio
@pytest.mark.parametrize("finish,expected", [(None, "without a finish"), ("content_filter", "abnormally")])
async def test_compat_eof_and_filter_are_not_success(finish, expected):
    client = OpenAICompatClient.__new__(OpenAICompatClient)
    client.model, client.max_output_tokens, client._request_options = "offline", 500, {}
    chunk = NS(usage=None, choices=[NS(finish_reason=finish, delta=NS(content="partial", tool_calls=None))])
    client._client = NS(chat=NS(completions=NS(create=AsyncMock(return_value=events([chunk])))))
    with pytest.raises(LLMError, match=expected):
        [event async for event in client.stream(ConversationManager())]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_after_completed_turn", [False, True])
async def test_failed_response_respects_complete_envelope_boundary(tmp_path, monkeypatch, failure_after_completed_turn):
    import asyncio

    from nanocursor.agent import Agent, LoopComplete
    from nanocursor.tools import create_default_registry

    written = asyncio.Event()
    registry = create_default_registry()
    write_tool = registry.get("WriteFile")
    original_execute = write_tool.execute
    writes = []

    async def execute(params):
        result = await original_execute(params)
        writes.append(result)
        written.set()
        return result

    monkeypatch.setattr(write_tool, "execute", execute)

    async def stream():
        yield NS(type="response.output_item.added", output_index=0,
                 item=NS(type="function_call", id="item", call_id="call", name="WriteFile"))
        yield NS(type="response.function_call_arguments.done", item_id="item", output_index=0,
                 arguments='{"file_path":"created.txt","content":"keep this result"}')
        await asyncio.sleep(0)
        yield terminal() if failure_after_completed_turn else terminal("failed")

    client = responses_client([])
    responses = [stream()]
    if failure_after_completed_turn:
        responses.append(events([terminal("failed")]))
    client._client.responses.create = AsyncMock(side_effect=responses)
    agent = Agent(client, registry, "openai", str(tmp_path), inject_environment_context=False)
    conversation = ConversationManager()
    conversation.add_user_message("Create a file")
    observed = []
    with pytest.raises(LLMError, match="model server failed"):
        async for event in agent.run(conversation, interactive=False):
            observed.append(event)
    assert not any(isinstance(event, LoopComplete) for event in observed)
    calls = [tool.tool_use_id for message in conversation.history for tool in message.tool_uses]
    results = [tool for message in conversation.history for tool in message.tool_results]
    if failure_after_completed_turn:
        assert (tmp_path / "created.txt").read_text() == "keep this result"
        assert len(writes) == 1 and not writes[0].is_error
        assert client._client.responses.create.await_count == 2
        assert calls == ["call"]
        assert len(results) == 1 and results[0].tool_use_id == "call" and not results[0].is_error
    else:
        assert not (tmp_path / "created.txt").exists()
        assert not writes and not calls and not results
        assert client._client.responses.create.await_count == 1
    assert agent._pending_tool_turn is None


@pytest.mark.asyncio
async def test_agent_output_limit_has_three_continuations_at_configured_cap(tmp_path):
    from nanocursor.agent import Agent, ErrorEvent, LoopComplete, RetryEvent
    from nanocursor.client import LLMClient
    from nanocursor.tools import ToolRegistry
    from nanocursor.tools.base import TextDelta

    class LimitedClient(LLMClient):
        max_output_tokens = 25

        def __init__(self):
            self.calls = 0
            self.caps = []

        def set_max_output_tokens(self, tokens):
            raise AssertionError("A run must not mutate the configured output cap")

        async def stream(self, conversation, **kwargs):
            self.calls += 1
            self.caps.append(self.max_output_tokens)
            yield TextDelta(f"part {self.calls}")
            yield StreamEnd("max_tokens", input_tokens=10, output_tokens=25)

    client = LimitedClient()
    agent = Agent(client, ToolRegistry(), "openai", str(tmp_path), inject_environment_context=False)
    conversation = ConversationManager()
    conversation.add_user_message("Long response")
    observed = [event async for event in agent.run(conversation, interactive=False)]
    assert client.calls == 4 and client.caps == [25] * 4
    assert len([event for event in observed if isinstance(event, RetryEvent)]) == 3
    assert any(isinstance(event, ErrorEvent) and event.code == "output_limit" for event in observed)
    assert not any(isinstance(event, LoopComplete) for event in observed)
    assert [message.content for message in conversation.history if message.role == "assistant"] == ["part 1", "part 2", "part 3", "part 4"]


@pytest.mark.asyncio
@pytest.mark.parametrize("summarizable", [False, True])
async def test_hard_context_limit_stops_when_summary_fails_or_no_prefix_exists(tmp_path, summarizable):
    import copy

    from nanocursor.agent import Agent, ErrorEvent, LoopComplete
    from nanocursor.client import LLMClient
    from nanocursor.context.manager import SUMMARY_PROMPT
    from nanocursor.tools import ToolRegistry

    class RejectSummaryClient(LLMClient):
        def __init__(self):
            self.calls = 0

        async def stream(self, conversation, *, system="", **kwargs):
            self.calls += 1
            assert system == SUMMARY_PROMPT, "Oversized conversation reached the ordinary request"
            yield StreamEnd("end_turn")

    client = RejectSummaryClient()
    conversation = ConversationManager()
    if summarizable:
        for i in range(20):
            conversation.add_user_message(f"constraint {i} " + "x" * 40_000)
    else:
        conversation.add_user_message("x" * 800_000)
    before = copy.deepcopy(conversation.history)
    agent = Agent(client, ToolRegistry(), "openai", str(tmp_path), inject_environment_context=False)
    observed = [event async for event in agent.run(conversation, interactive=False)]
    assert client.calls == (1 if summarizable else 0)
    assert any(isinstance(event, ErrorEvent) and event.fatal and event.code == "compact_failed" for event in observed)
    assert not any(isinstance(event, LoopComplete) for event in observed)
    assert conversation.history == before


@pytest.mark.asyncio
async def test_small_context_window_still_allows_short_requests(tmp_path):
    from nanocursor.agent import Agent, LoopComplete
    from nanocursor.client import LLMClient
    from nanocursor.context.manager import compute_compact_threshold
    from nanocursor.tools import ToolRegistry
    from nanocursor.tools.base import TextDelta

    class ShortClient(LLMClient):
        async def stream(self, conversation, **kwargs):
            yield TextDelta("Done")
            yield StreamEnd("end_turn")

    assert 0 < compute_compact_threshold(4096) < compute_compact_threshold(4096, manual=True) < 4096
    agent = Agent(ShortClient(), ToolRegistry(), "openai", str(tmp_path), context_window=4096,
                  inject_environment_context=False)
    conversation = ConversationManager()
    conversation.add_user_message("Hello")
    observed = [event async for event in agent.run(conversation, interactive=False)]
    assert any(isinstance(event, LoopComplete) for event in observed)
