from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
from pydantic import ValidationError

from nanocursor.client import LLMClient
from nanocursor.conversation import ConversationManager
from nanocursor.eval.bridge_client import BridgeClient
from nanocursor.eval.contract import CommandRunParams, ISSUE_AGENT_SYSTEM_PROMPT, TOOL_NAMES
from nanocursor.eval.runner import run_evaluation
from nanocursor.eval.tools import create_eval_registry
from nanocursor.tools.base import StreamEnd, StreamEvent, TextDelta


class RecordingClient(LLMClient):
    def __init__(self) -> None:
        self.system = ""
        self.tools: list[dict[str, Any]] = []
        self.messages: list[str] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.system = system
        self.tools = tools or []
        self.messages = [message.content for message in conversation.get_messages()]
        yield TextDelta(text="done")
        yield StreamEnd(stop_reason="end_turn", input_tokens=12, output_tokens=3)


def bridge_transport(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200, json={"ok": True, "protocolVersion": "1"})
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "ok": True,
            "protocolVersion": "1",
            "toolCallId": body["toolCallId"],
            "tool": body["tool"],
            "durationMs": 4,
            "result": "README.md",
        },
    )


@pytest.mark.asyncio
async def test_eval_registry_exposes_only_contract_tools() -> None:
    transport = httpx.MockTransport(bridge_transport)
    async with BridgeClient("http://127.0.0.1:9000", "token", transport=transport) as bridge:
        registry = create_eval_registry(bridge)
        assert tuple(tool.name for tool in registry.list_tools()) == TOOL_NAMES
        assert all(not tool.is_concurrency_safe for tool in registry.list_tools())
        tool = registry.get("repo_list")
        assert tool is not None
        result = await tool.execute(tool.params_model())
        assert result.output == '"README.md"'
        assert not result.is_error


def test_command_contract_rejects_shell_like_empty_arguments() -> None:
    with pytest.raises(ValidationError):
        CommandRunParams(argv=["pytest", ""])


@pytest.mark.asyncio
async def test_mock_run_uses_shared_prompt_without_host_environment(tmp_path: Path) -> None:
    transport = httpx.MockTransport(bridge_transport)
    client = RecordingClient()
    async with BridgeClient("http://127.0.0.1:9000", "token", transport=transport) as bridge:
        summary = await run_evaluation(
            client=client,
            protocol="openai-compat",
            model="mock-model",
            prompt="Fix the repository issue.",
            task_id="mock-task",
            run_id="mock-run",
            bridge_client=bridge,
            output_dir=tmp_path,
            max_turns=4,
            max_wall_time_seconds=10,
        )

    assert client.system == ISSUE_AGENT_SYSTEM_PROMPT
    assert client.messages == ["Fix the repository issue."]
    assert [tool["name"] for tool in client.tools] == list(TOOL_NAMES)
    assert summary.status == "completed"
    assert summary.turns_used == 1
    assert summary.input_tokens == 12
    assert summary.output_tokens == 3
    assert summary.final_response == "done"
    assert (tmp_path / "mock-run.trace.jsonl").exists()
    assert (tmp_path / "mock-run.summary.json").exists()
