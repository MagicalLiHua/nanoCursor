from __future__ import annotations

from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator

from nanocursor.application.session import SessionPersistence
from nanocursor.events import (
    AgentEvent,
    CompactNotification,
    LoopComplete,
    MemoryContextChanged,
    TurnComplete,
)

if TYPE_CHECKING:
    from nanocursor.agent import Agent
    from nanocursor.conversation import ConversationManager


@dataclass
class ForegroundRun:
    agent: Agent
    persistence: SessionPersistence | None = None

    async def events(self, conversation: ConversationManager, *, interactive: bool = True,
                     source: str = "user") -> AsyncIterator[AgentEvent]:
        try:
            async with aclosing(self.agent.run(conversation, interactive=interactive, source=source)) as events:
                async for event in events:
                    if self.persistence is not None:
                        if isinstance(event, CompactNotification):
                            self.persistence.commit_compact(event)
                        elif isinstance(event, MemoryContextChanged):
                            self.persistence.commit_memory(event)
                        elif isinstance(event, (TurnComplete, LoopComplete)):
                            self.persistence.flush()
                            if isinstance(event, LoopComplete) and self.persistence.session is not None:
                                self.persistence.session.meta.total_tokens = (
                                    self.agent.total_input_tokens + self.agent.total_output_tokens
                                )
                    yield event
        finally:
            if self.persistence is not None:
                self.persistence.flush()
