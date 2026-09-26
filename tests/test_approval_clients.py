"""Wire-shape/closure tests against fake SDKs, with no network access."""
from dataclasses import asdict
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from nanocursor.client import complete_review
from nanocursor.config import ProviderConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["anthropic", "openai", "openai-compat"])
@pytest.mark.parametrize("complete", [True, False])
async def test_isolated_request_has_no_tools_retries_or_mutated_config(monkeypatch, protocol, complete):
    config = ProviderConfig("review", protocol, "https://invalid.example", "model", "secret", thinking=True,
                            max_output_tokens=9000)
    before = asdict(config)
    if protocol == "anthropic":
        response = NS(content=[NS(type="text", text='{"decision":"ask"}')],
                      stop_reason="end_turn" if complete else "max_tokens",
                      usage=NS(input_tokens=12, output_tokens=15))
    elif protocol == "openai":
        response = NS(status="completed" if complete else "incomplete", output_text='{"decision":"ask"}',
                      output=[NS(type="message", content=[NS(type="output_text")])],
                      usage=NS(input_tokens=12, output_tokens=15))
    else:
        response = NS(choices=[NS(finish_reason="stop" if complete else "length",
                                 message=NS(content='{"decision":"ask"}', tool_calls=None))],
                      usage=NS(prompt_tokens=12, completion_tokens=15))
    call = AsyncMock(return_value=response)
    client = NS(messages=NS(create=call), responses=NS(create=call), chat=NS(completions=NS(create=call)))
    class Scope:
        closed = False
        async def __aenter__(self):
            return client
        async def __aexit__(self, *args):
            self.closed = True
    scope = Scope()
    factory = Mock(return_value=scope)
    monkeypatch.setattr("nanocursor.client.AsyncAnthropic" if protocol == "anthropic" else
                        "nanocursor.client.AsyncOpenAI", factory)
    result = await complete_review(config, "policy", "payload", 10)
    assert result.complete is complete
    assert result.input_tokens == 12 and result.output_tokens == 15
    assert scope.closed
    assert factory.call_args.kwargs["max_retries"] == 0
    assert factory.call_args.kwargs["timeout"] == 10
    assert asdict(config) == before
    assert "tools" not in call.call_args.kwargs
    assert "thinking" not in call.call_args.kwargs
    assert "temperature" not in call.call_args.kwargs
    assert "stream" not in call.call_args.kwargs
    call.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["refusal", "function_call"])
async def test_responses_unrequested_tool_or_refusal_is_incomplete(monkeypatch, kind):
    output = [NS(type="message", content=[NS(type="refusal")])] if kind == "refusal" else [NS(type=kind)]
    sdk = NS(responses=NS(create=AsyncMock(return_value=NS(
        status="completed", output_text='{"decision":"allow"}', output=output, usage=None))))
    class Scope:
        async def __aenter__(self): return sdk
        async def __aexit__(self, *args): pass
    monkeypatch.setattr("nanocursor.client.AsyncOpenAI", lambda **kwargs: Scope())
    result = await complete_review(ProviderConfig("r", "openai", "https://invalid", "m", "key"), "s", "p", 10)
    assert not result.complete
