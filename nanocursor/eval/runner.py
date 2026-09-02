from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nanocursor.agent import (
    Agent,
    ErrorEvent,
    LoopComplete,
    PermissionRequest,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from nanocursor.client import LLMClient
from nanocursor.conversation import ConversationManager
from nanocursor.eval.bridge_client import BridgeClient
from nanocursor.eval.contract import ISSUE_AGENT_SYSTEM_PROMPT
from nanocursor.eval.tools import create_eval_registry
from nanocursor.eval.trace import EvalRunSummary, TraceRecorder, utc_now


async def run_evaluation(
    *,
    client: LLMClient,
    protocol: str,
    model: str,
    prompt: str,
    task_id: str,
    run_id: str,
    bridge_client: BridgeClient,
    output_dir: Path,
    max_turns: int = 96,
    max_wall_time_seconds: int = 1_200,
    context_window: int = 128_000,
) -> EvalRunSummary:
    recorder = TraceRecorder(output_dir, run_id)
    registry = create_eval_registry(bridge_client)
    agent = Agent(
        client=client,
        registry=registry,
        protocol=protocol,
        work_dir=str(output_dir),
        max_iterations=max_turns,
        context_window=context_window,
        system_prompt_override=ISSUE_AGENT_SYSTEM_PROMPT,
        inject_environment_context=False,
    )
    conversation = ConversationManager()
    conversation.add_user_message(prompt)
    started_at = utc_now()
    recorder.record(
        "run.started",
        {
            "runId": run_id,
            "taskId": task_id,
            "model": model,
            "protocol": protocol,
            "maxTurns": max_turns,
            "maxWallTimeSeconds": max_wall_time_seconds,
            "tools": [tool.name for tool in registry.list_tools()],
        },
    )
    current_text = ""
    final_response = ""
    turns_used = 0
    input_tokens = 0
    output_tokens = 0
    tool_calls = 0
    tool_errors = 0
    errors: list[str] = []
    status = "completed"

    async def consume() -> None:
        nonlocal current_text, final_response, turns_used
        nonlocal input_tokens, output_tokens, tool_calls, tool_errors, status
        async for event in agent.run(conversation):
            if isinstance(event, StreamText):
                current_text += event.text
                recorder.record("agent.text_delta", {"text": event.text})
            elif isinstance(event, ThinkingText):
                recorder.record("agent.thinking_delta", {"text": event.text})
            elif isinstance(event, ToolUseEvent):
                tool_calls += 1
                recorder.record(
                    "agent.tool_call",
                    {
                        "toolCallId": event.tool_id,
                        "tool": event.tool_name,
                        "arguments": event.arguments,
                    },
                )
            elif isinstance(event, ToolResultEvent):
                if event.is_error:
                    tool_errors += 1
                recorder.record(
                    "agent.tool_result",
                    {
                        "toolCallId": event.tool_id,
                        "tool": event.tool_name,
                        "output": event.output,
                        "isError": event.is_error,
                        "elapsedSeconds": event.elapsed,
                    },
                )
            elif isinstance(event, UsageEvent):
                input_tokens = event.input_tokens
                output_tokens = event.output_tokens
                recorder.record(
                    "agent.usage",
                    {"inputTokens": input_tokens, "outputTokens": output_tokens},
                )
            elif isinstance(event, TurnComplete):
                turns_used = event.turn
                recorder.record("agent.turn_complete", {"turn": event.turn})
                current_text = ""
            elif isinstance(event, LoopComplete):
                turns_used = event.total_turns
                final_response = current_text
                recorder.record("agent.loop_complete", {"turns": event.total_turns})
            elif isinstance(event, ErrorEvent):
                status = "error"
                errors.append(event.message)
                recorder.record("agent.error", {"message": event.message})
            elif isinstance(event, PermissionRequest):
                status = "error"
                message = f"Unexpected permission request for {event.tool_name}."
                errors.append(message)
                recorder.record("agent.error", {"message": message})
                event.future.cancel()

    try:
        async with asyncio.timeout(max_wall_time_seconds):
            await consume()
    except TimeoutError:
        status = "timeout"
        errors.append(f"Agent exceeded wall-time limit of {max_wall_time_seconds} seconds.")
        recorder.record("run.timeout", {"maxWallTimeSeconds": max_wall_time_seconds})
    except Exception as error:
        status = "error"
        errors.append(str(error))
        recorder.record("run.error", {"message": str(error)})

    summary = EvalRunSummary(
        run_id=run_id,
        task_id=task_id,
        status=status,
        started_at=started_at,
        finished_at=utc_now(),
        model=model,
        max_turns=max_turns,
        max_wall_time_seconds=max_wall_time_seconds,
        turns_used=turns_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=tool_calls,
        tool_errors=tool_errors,
        final_response=final_response,
        errors=errors,
    )
    recorder.record("run.finished", {"status": status, "turnsUsed": turns_used})
    recorder.write_summary(summary)
    return summary


def registry_contract(bridge_client: BridgeClient) -> list[dict[str, Any]]:
    return create_eval_registry(bridge_client).get_all_schemas("openai-compat")
