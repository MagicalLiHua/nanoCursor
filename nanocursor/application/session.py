from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from nanocursor.conversation import ConversationManager, Message
from nanocursor.events import CompactNotification, MemoryContextChanged
from nanocursor.memory.session import Session, SessionMetadataError, make_compact_boundary, make_history_boundary

if TYPE_CHECKING:
    from nanocursor.agent import Agent
    from nanocursor.recovery import RecoveryRuntime
    from nanocursor.tools import ToolRegistry


class SessionController:
    def __init__(self, recovery: RecoveryRuntime | None = None) -> None:
        self.recovery = recovery

    def bind(self, session: Session, agent: Agent | None, *, registry: ToolRegistry | None = None,
             on_bound: Callable[[Session], None] | None = None) -> None:
        if self.recovery:
            self.recovery.bind_session(session)
        if on_bound is not None:
            on_bound(session)
        if agent is None:
            return
        from nanocursor.filehistory import FileHistory

        history = FileHistory(
            agent.work_dir, session.session_id,
            store=self.recovery.store if self.recovery else None,
            workspace_id=self.recovery.workspace_id if self.recovery else None,
            generation=self.recovery.generation if self.recovery else 0,
        )
        if self.recovery:
            self.recovery.file_history = history
        if registry is not None and registry is not agent.registry:
            for tool in registry.list_tools():
                if hasattr(tool, "file_history"):
                    tool.file_history = history
        agent.bind_session(session, history)

    def set_conversation(self, conversation: ConversationManager, agent: Agent | None) -> None:
        if agent is not None:
            agent.synchronize_memory_context(conversation)


@dataclass
class SessionPersistence:
    session: Session | None
    conversation: ConversationManager
    report_metadata_error: Callable[[str], None]
    history_cursor: int = 0

    def append_message(self, message: Message) -> None:
        if self.session is None:
            return
        try:
            if getattr(message, "_durable_notification", None):
                from nanocursor.agents.task_manager import persist_notification_message
                persist_notification_message(self.session, message)
            else:
                self.session.append(message)
        except SessionMetadataError as exc:
            self.report_metadata_error(f"会话正文已保存，但元数据更新失败: {exc}")

    def flush(self) -> None:
        if self.session is None:
            return
        for message in self.conversation.history[self.history_cursor:]:
            self.append_message(message)
            self.history_cursor += 1

    def commit_compact(self, event: CompactNotification, *, include_messages: bool = False) -> None:
        notice = None
        try:
            if self.session is not None and event.boundary is not None:
                options = {"messages": self.conversation.history} if include_messages else {}
                record = make_compact_boundary(event.boundary.summary, event.boundary.keep, **options)
                self.session.append_record(record)
        except SessionMetadataError as exc:
            notice = f"压缩结果已保存，但会话元数据更新失败: {exc}"
        except Exception:
            if event.prior_conversation is not None:
                self.conversation.restore_state(event.prior_conversation)
            raise
        self.history_cursor = len(self.conversation.history)
        if notice is not None:
            self.report_metadata_error(notice)

    def commit_memory(self, event: MemoryContextChanged) -> None:
        notice = None
        try:
            if self.session is not None:
                self.session.append_record(make_history_boundary(self.conversation.history))
        except SessionMetadataError as exc:
            notice = f"记忆上下文已保存，但元数据更新失败: {exc}"
        except Exception:
            self.conversation.restore_state(event.prior_conversation)
            self.history_cursor = len(self.conversation.history)
            raise
        self.history_cursor = len(self.conversation.history)
        if notice is not None:
            self.report_metadata_error(notice)
