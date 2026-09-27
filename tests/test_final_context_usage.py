from __future__ import annotations

import pytest

from nanocursor.agent import Agent, LoopComplete, UsageEvent
from nanocursor.conversation import ConversationManager, Message, estimate_tokens
from nanocursor.tools import ToolRegistry
from nanocursor.tools.base import StreamEnd, TextDelta
from test_agent import MockLLMClient


@pytest.mark.asyncio
async def test_text_only_turns_anchor_latest_context_without_resetting_total_usage(tmp_path):
    client = MockLLMClient([
        [TextDelta("First answer"), StreamEnd("end_turn", 100, 20, 300, 40)],
        [TextDelta("Second answer"), StreamEnd("end_turn", 150, 30, 400, 50)],
    ])
    agent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(tmp_path),
                  inject_environment_context=False)
    conversation = ConversationManager()
    conversation.add_user_message("First question")

    first = [event async for event in agent.run(conversation)]
    assert isinstance(first[-1], LoopComplete)
    assert conversation.current_tokens() == 460
    assert conversation.anchor_count == len(conversation.history)

    conversation.add_user_message("x" * 350)
    assert conversation.current_tokens() == 560
    second = [event async for event in agent.run(conversation)]

    assert isinstance(second[-1], LoopComplete)
    assert conversation.current_tokens() == 630
    assert conversation.anchor_count == len(conversation.history)
    usage = next(event for event in second if isinstance(event, UsageEvent))
    assert (usage.input_tokens, usage.output_tokens) == (250, 50)
    assert (agent.total_input_tokens, agent.total_output_tokens) == (250, 50)


@pytest.mark.asyncio
async def test_final_reply_reanchors_replaced_history_after_output_recovery(tmp_path):
    client = MockLLMClient([
        [TextDelta("Partial answer"), StreamEnd("max_tokens", 120, 50, 200, 10)],
        [TextDelta("Completed answer"), StreamEnd("end_turn", 170, 30, 250, 20)],
    ])
    agent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(tmp_path),
                  inject_environment_context=False)
    conversation = ConversationManager()
    conversation.add_user_message("Old context")
    conversation.record_usage_anchor(9000, 200)
    conversation.replace_history([Message("user", "Compacted context")])
    assert conversation.current_tokens() == estimate_tokens(conversation.history)

    async for event in agent.run(conversation):
        if isinstance(event, LoopComplete):
            assert conversation.current_tokens() == 470
            assert conversation.anchor_count == len(conversation.history)

    assert conversation.history[-1].content == "Completed answer"
    assert (agent.total_input_tokens, agent.total_output_tokens) == (290, 80)
