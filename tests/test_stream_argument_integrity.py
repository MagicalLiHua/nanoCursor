from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from mcp import types as mcp_types
from pydantic import ValidationError

from nanocursor.client import OpenAIClient, AnthropicClient, _complete_tool_call
from nanocursor.conversation import ConversationManager
from nanocursor.mcp.tool_wrapper import MCPToolWrapper, _build_params_model
from nanocursor.tools.base import ToolCallComplete


async def events(items):
    for item in items:
        yield item


def added(i):
    return NS(type="response.output_item.added", output_index=i,
              item=NS(type="function_call", id=f"item{i}", call_id=f"call{i}", name=f"tool{i}"))


def delta(i, text):
    return NS(type="response.function_call_arguments.delta", item_id=f"item{i}", output_index=i, delta=text)


def done(i, text):
    return NS(type="response.function_call_arguments.done", item_id=f"item{i}", output_index=i, arguments=text)


async def responses(items):
    client = OpenAIClient.__new__(OpenAIClient)
    client.model = "offline"
    client._client = NS(responses=NS(create=AsyncMock(return_value=events(items))))
    return [e async for e in client.stream(ConversationManager()) if isinstance(e, ToolCallComplete)]


@pytest.mark.asyncio
async def test_responses_interleaving_preserves_call_identity():
    items = [added(0), delta(0, '{"a":'), added(1), delta(1, '{"b":2}'),
             delta(0, '1}'), done(1, '{"b":2}'), done(0, '{"a":1}')]
    calls = await responses(items)
    assert [(c.tool_id, c.tool_name, c.arguments) for c in calls] == [
        ("call1", "tool1", {"b": 2}), ("call0", "tool0", {"a": 1})]


@pytest.mark.asyncio
async def test_done_is_authoritative_and_duplicate_does_not_execute_twice():
    calls = await responses([added(0), delta(0, '{truncated'), done(0, '{"ok":true}'), done(0, '{"ok":true}')])
    assert len(calls) == 1 and calls[0].arguments == {"ok": True}
    calls = await responses([added(0), done(0, '{"ok":true}')])
    assert calls[0].arguments == {"ok": True}


@pytest.mark.parametrize("raw", ['', '{bad', '{"a":', '[]', 'null', '"string"'])
def test_invalid_arguments_keep_raw_error(raw):
    call = _complete_tool_call("id", "tool", raw)
    assert call.arguments_error and call.raw_arguments == raw


@pytest.mark.asyncio
async def test_every_json_character_boundary():
    raw = '{"path":"目录/one.txt","text":"a\\nb\\\"c"}'
    for split in range(len(raw) + 1):
        calls = await responses([added(0), delta(0, raw[:split]), delta(0, raw[split:]),
                                 NS(type="response.function_call_arguments.done", item_id="item0", output_index=0)])
        assert len(calls) == 1 and calls[0].arguments["path"] == "目录/one.txt"
        assert not calls[0].arguments_error


@pytest.mark.asyncio
async def test_anthropic_interleaved_indices():
    items = [
        NS(type="content_block_start", index=i, content_block=NS(type="tool_use", id=f"id{i}", name=f"tool{i}", input={}))
        for i in (0, 1)
    ]
    items += [NS(type="content_block_delta", index=i, delta=NS(type="input_json_delta", partial_json=f'{{"n":{i}}}')) for i in (0, 1)]
    items += [NS(type="content_block_stop", index=i) for i in (1, 0)]

    class Stream:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        def __aiter__(self):
            return events(items)
        async def get_final_message(self):
            return NS(usage=NS(input_tokens=1, output_tokens=1), stop_reason="tool_use")

    client = AnthropicClient.__new__(AnthropicClient)
    client.model, client.max_output_tokens, client.thinking = "offline", 1024, False
    client._client = NS(messages=NS(stream=lambda **kwargs: Stream()))
    calls = [e async for e in client.stream(ConversationManager()) if isinstance(e, ToolCallComplete)]
    assert [(c.tool_id, c.arguments) for c in calls] == [("id1", {"n": 1}), ("id0", {"n": 0})]


SCHEMA = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["read"]},
    "count": {"type": "integer", "minimum": 1},
    "nested": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["ids"]},
    "nullable": {"type": ["string", "null"]}},
    "required": ["kind", "count", "nested"], "additionalProperties": False}


@pytest.mark.parametrize("override", [{"kind": "delete"}, {"count": 0}, {"count": "1"},
                                     {"nested": {"ids": ["wrong"]}}, {"unknown": 1}])
def test_mcp_validates_original_schema(override):
    model = _build_params_model("test", SCHEMA)
    args = {"kind": "read", "count": 1, "nested": {"ids": [1]}, **override}
    with pytest.raises(ValidationError):
        model.model_validate(args)


@pytest.mark.asyncio
async def test_mcp_keeps_explicit_null_and_additional_allowed_properties():
    schema = {"type": "object", "properties": {"optional": {"type": ["string", "null"]}}}
    client = NS(is_alive=True, call_tool=AsyncMock(return_value=NS(content=[], isError=False)))
    wrapper = MCPToolWrapper("test", mcp_types.Tool(name="echo", inputSchema=schema), client)
    params = wrapper.validate_arguments({"optional": None, "extra": {"x": 1}})
    await wrapper.execute(params)
    client.call_tool.assert_awaited_once_with("echo", {"optional": None, "extra": {"x": 1}})


@pytest.mark.asyncio
async def test_incomplete_stream_does_not_dispatch_partial_arguments():
    from nanocursor.client import LLMError
    with pytest.raises(LLMError, match="before tool arguments completed"):
        await responses([added(0), delta(0, '{"a":')])
