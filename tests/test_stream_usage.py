"""Chat Completions usage can share a content chunk or arrive on its own."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from openai.types.chat import ChatCompletionChunk

from nanocursor.agent import Agent, StreamCollector, UsageEvent
from nanocursor.client import OpenAIClient, OpenAICompatClient
from nanocursor.conversation import ConversationManager
from nanocursor.tools import ToolRegistry
from nanocursor.tools.base import StreamEnd, TextDelta, ToolCallComplete


def chunk(*, content=None, finish=None, usage=None, usage_only=False, tool_calls=None):
    return ChatCompletionChunk.model_validate({
        "id": "test-response", "created": 0, "model": "deepseek-v4-flash",
        "object": "chat.completion.chunk",
        "choices": [] if usage_only else [{
            "index": 0, "finish_reason": finish,
            "delta": {"content": content, "tool_calls": tool_calls},
        }],
        "usage": usage,
    })


def usage(prompt=1_500, output=50, **extra):
    return {"prompt_tokens": prompt, "completion_tokens": output,
            "total_tokens": prompt + output, **extra}


def client_for(chunks):
    async def response():
        for item in chunks:
            yield item

    client = object.__new__(OpenAICompatClient)
    client.model = "deepseek-v4-flash"
    client.max_output_tokens = 8_000
    client._request_options = {}
    client._client = NS(chat=NS(completions=NS(create=AsyncMock(return_value=response()))))
    return client


@pytest.mark.asyncio
async def test_deepseek_usage_on_last_choice_chunk_reaches_collector():
    client = client_for([
        chunk(content="Hello"),
        chunk(content="!", finish="stop", usage=usage(
            prompt_tokens_details={"cached_tokens": 500},
            prompt_cache_hit_tokens=500, prompt_cache_miss_tokens=1_000)),
    ])
    collector = StreamCollector()
    events = [event async for event in collector.consume(client.stream(ConversationManager()))]
    assert collector.response.text == "Hello!"
    assert (collector.response.input_tokens, collector.response.output_tokens,
            collector.response.cache_read) == (1_000, 50, 500)
    assert collector.response.stop_reason == "end_turn"
    assert events
    assert client._client.chat.completions.create.call_args.kwargs["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_openai_usage_only_chunk_retains_accounting():
    events = [event async for event in client_for([
        chunk(content="Done", finish="stop"),
        chunk(usage_only=True, usage=usage(prompt_tokens_details={"cached_tokens": 400})),
    ]).stream(ConversationManager())]
    ends = [event for event in events if isinstance(event, StreamEnd)]
    assert ends == [StreamEnd("end_turn", 1_100, 50, 400, 0)]
    assert isinstance(events[0], TextDelta)


@pytest.mark.asyncio
async def test_repeated_usage_is_a_snapshot_not_an_additional_charge():
    events = [event async for event in client_for([
        chunk(content="Done", finish="stop", usage=usage(prompt=500, output=10)),
        chunk(usage_only=True, usage=usage(prompt=500, output=20)),
        chunk(usage_only=True, usage=usage(prompt=500, output=20)),
    ]).stream(ConversationManager())]
    assert [event for event in events if isinstance(event, StreamEnd)] == [StreamEnd("end_turn", 500, 20)]


@pytest.mark.asyncio
async def test_no_usage_preserves_finish_without_fabricated_tokens():
    events = [event async for event in client_for([
        chunk(content="Answer", finish="stop"),
    ]).stream(ConversationManager())]
    assert [event for event in events if isinstance(event, StreamEnd)] == [
        StreamEnd("end_turn", usage_available=False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,cache", [
    ({"prompt_cache_hit_tokens": 600, "prompt_cache_miss_tokens": 900}, 600),
    ({"prompt_tokens_details": {"cached_tokens": 600}}, 600),
    ({"prompt_tokens_details": {"cached_tokens": 600}, "prompt_cache_hit_tokens": 600}, 600),
    ({"prompt_tokens_details": {}, "prompt_cache_hit_tokens": 600}, 600),
    ({}, 0),
])
async def test_cache_counts_support_deepseek_and_standard_fields(extra, cache):
    events = [event async for event in client_for([
        chunk(finish="stop", usage=usage(**extra)),
    ]).stream(ConversationManager())]
    end = next(event for event in events if isinstance(event, StreamEnd))
    assert end.cache_read == cache
    assert end.input_tokens + end.cache_read + end.cache_creation == 1_500
    assert end.output_tokens == 50


@pytest.mark.asyncio
async def test_token_limit_stop_reason_survives_separate_usage_chunk():
    events = [event async for event in client_for([
        chunk(content="Partial reply", finish="length"),
        chunk(usage_only=True, usage=usage()),
    ]).stream(ConversationManager())]
    end = next(event for event in events if isinstance(event, StreamEnd))
    assert end.stop_reason == "max_tokens"
    assert end.output_tokens == 50


@pytest.mark.asyncio
async def test_tool_call_completion_and_usage_both_survive_shared_chunk():
    events = [event async for event in client_for([
        chunk(tool_calls=[{"index": 0, "id": "call-1", "type": "function",
                           "function": {"name": "ReadFile", "arguments": '{"file_path":"a.txt"}'}}]),
        chunk(finish="tool_calls", usage=usage()),
    ]).stream(ConversationManager())]
    completed = [event for event in events if isinstance(event, ToolCallComplete)]
    assert len(completed) == 1
    assert completed[0].arguments == {"file_path": "a.txt"}
    assert events[-1] == StreamEnd("tool_use", 1_500, 50)


@pytest.mark.asyncio
async def test_real_compat_client_updates_agent_usage_and_context(tmp_path):
    client = client_for([
        chunk(content="Hello"),
        chunk(finish="stop", usage=usage(prompt_cache_hit_tokens=500)),
    ])
    agent = Agent(client, ToolRegistry(), protocol="openai-compat", work_dir=str(tmp_path),
                  inject_environment_context=False)
    conversation = ConversationManager()
    conversation.add_user_message("Say hello")
    events = [event async for event in agent.run(conversation, interactive=False)]
    assert (agent.total_input_tokens, agent.total_output_tokens) == (1_000, 50)
    assert conversation.current_tokens() == 1_550
    assert [event for event in events if isinstance(event, UsageEvent)] == [UsageEvent(1_000, 50)]


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_usage,expected", [
    (None, StreamEnd("end_turn", usage_available=False)),
    (NS(input_tokens=1_500, output_tokens=50, input_tokens_details=NS(cached_tokens=500)),
     StreamEnd("end_turn", 1_000, 50, 500)),
])
async def test_responses_api_distinguishes_missing_usage(reported_usage, expected):
    async def response():
        yield NS(type="response.completed", response=NS(usage=reported_usage))

    client = object.__new__(OpenAIClient)
    client.model = "test-model"
    client.max_output_tokens = 8_000
    client._request_options = {}
    client._client = NS(responses=NS(create=AsyncMock(return_value=response())))
    events = [event async for event in client.stream(ConversationManager())]
    assert events == [expected]
