from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Mapping
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

from nanocursor.commands.ports import CommandServices, command_services

if TYPE_CHECKING:
    from nanocursor.agent import Agent
    from nanocursor.conversation import ConversationManager
    from nanocursor.memory.auto_memory import MemoryManager
    from nanocursor.memory.session import Session, SessionManager


class CommandType(str, Enum):
    LOCAL = "local"
    LOCAL_UI = "local_ui"
    PROMPT = "prompt"


class UIController(Protocol):
    def add_system_message(self, text: str) -> None: ...


    def send_user_message(self, text: str) -> None: ...
    def send_skill_message(self, display_text: str, name: str, prompt: str) -> None: ...
    def set_plan_mode(self, enabled: bool) -> None: ...
    def get_token_count(self) -> tuple[int, int]: ...
    def refresh_status(self) -> None: ...


@dataclass
class CommandContext:
    args: str
    agent: Agent | None
    conversation: ConversationManager | None
    session: Session | None
    session_manager: SessionManager | None
    memory_manager: MemoryManager | None
    ui: UIController
    config: CommandServices | Mapping[str, object]

    @property
    def services(self) -> CommandServices:
        return command_services(self)


CommandHandler = Callable[[CommandContext], Awaitable[None]]


@dataclass
class Command:
    name: str
    description: str
    type: CommandType
    handler: CommandHandler
    aliases: list[str] = field(default_factory=list)
    usage: str = ""
    arg_prompt: str = ""
    hidden: bool = False


class CommandRegistry:


    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._alias_map: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def register(self, command: Command) -> None:
        async with self._lock:
            if command.name in self._commands or command.name in self._alias_map:
                raise ValueError(
                    f"Command name '{command.name}' conflicts with an existing command or alias"
                )
            for alias in command.aliases:
                if alias in self._alias_map or alias in self._commands:
                    raise ValueError(
                        f"Alias '{alias}' conflicts with an existing command or alias"
                    )
            self._commands[command.name] = command
            for alias in command.aliases:
                self._alias_map[alias] = command.name

    def register_sync(self, command: Command) -> None:
        if command.name in self._commands or command.name in self._alias_map:
            raise ValueError(
                f"Command name '{command.name}' conflicts with an existing command or alias"
            )
        for alias in command.aliases:
            if alias in self._alias_map or alias in self._commands:
                raise ValueError(
                    f"Alias '{alias}' conflicts with an existing command or alias"
                )
        self._commands[command.name] = command
        for alias in command.aliases:
            self._alias_map[alias] = command.name


    def find(self, name: str) -> Command | None:
        if name in self._commands:
            return self._commands[name]
        canon = self._alias_map.get(name)
        if canon:
            return self._commands.get(canon)
        return None


    def list_commands(self) -> list[Command]:
        return [cmd for cmd in self._commands.values() if not cmd.hidden]
