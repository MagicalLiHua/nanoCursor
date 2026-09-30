from __future__ import annotations

import asyncio
from contextlib import aclosing
from copy import deepcopy
from dataclasses import fields
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import Mock

import pytest

from nanocursor import agent as legacy_events
from nanocursor import events
from nanocursor.agent import Agent
from nanocursor.agents.factory import AgentFactory, SpawnContext
from nanocursor.agents.task_manager import TaskManager
from nanocursor.agents.trace import TraceManager
from nanocursor.application.background import BackgroundOptions, assemble_background
from nanocursor.application.bootstrap import AgentDependencies, AgentSettings, assemble_agent, create_permissions
from nanocursor.application.execution import ForegroundRun
from nanocursor.application.session import SessionController, SessionPersistence
from nanocursor.commands.handlers.clear import handle_clear
from nanocursor.commands.handlers.session import handle_session
from nanocursor.commands.ports import CommandServices, SessionActions
from nanocursor.commands.registry import CommandContext
from nanocursor.config import ProviderConfig, SandboxAppConfig
from nanocursor.context import CompactBoundary
from nanocursor.conversation import ConversationManager, Message
from nanocursor.events import CompactNotification, LoopComplete, MemoryContextChanged, StreamText, TurnComplete
from nanocursor.memory.session import SessionManager, SessionMetadataError
from nanocursor.permissions import PermissionMode
from nanocursor.tools import ToolRegistry, create_default_registry
from nanocursor.worktree.manager import WorktreeManager
from test_agent import MockLLMClient


def make_agent(tmp_path):
    return Agent(MockLLMClient([]), create_default_registry(), "anthropic", work_dir=str(tmp_path))


@pytest.mark.parametrize("name", [
    "AgentEvent", "AgentRunError", "StreamText", "ThinkingText", "RetryEvent",
    "ToolUseEvent", "ToolResultEvent", "TurnComplete", "LoopComplete", "UsageEvent",
    "ErrorEvent", "CompactNotification", "MemoryContextChanged", "HookEvent",
    "PermissionResponse", "PermissionRequest", "PermissionCall",
])
def test_old_event_imports_preserve_identity(name):
    assert getattr(legacy_events, name) is getattr(events, name)


def test_event_fields_and_permission_future_are_unchanged():
    assert [item.name for item in fields(events.PermissionRequest)] == [
        "tool_name", "description", "future", "reason", "cwd", "allow_always", "allow_edits",
    ]
    assert [item.name for item in fields(events.CompactNotification)] == [
        "before_tokens", "message", "boundary", "prior_conversation",
    ]


def test_event_annotations_resolve_without_private_engine_names():
    assert CompactBoundary is events.CompactBoundary
    hints = get_type_hints(events.CompactNotification)
    assert hints["boundary"] == CompactBoundary | None
    assert hints["prior_conversation"] == ConversationManager | None


@pytest.mark.asyncio
async def test_permission_future_is_the_original_object():
    future = asyncio.get_running_loop().create_future()
    request = events.PermissionRequest("Bash", "command", future)
    request.future.set_result(legacy_events.PermissionResponse.DENY)
    assert await future is events.PermissionResponse.DENY


def test_shared_assembly_keeps_explicit_dependencies_and_frontend_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    provider = ProviderConfig("local", "anthropic", "https://invalid.example", "model", context_window=32_000)
    settings = AgentSettings(provider, str(tmp_path), str(tmp_path / "state"), tmp_path / "rules",
                             PermissionMode.PLAN, SandboxAppConfig(), "project instructions")
    client, registry = MockLLMClient([]), ToolRegistry()
    permissions = create_permissions(settings)
    services = assemble_agent(settings, AgentDependencies(client, registry), permissions=permissions)
    assert services.agent.client is client
    assert services.agent.registry is registry
    assert services.agent.permission_checker is permissions
    assert services.agent.permission_mode is PermissionMode.PLAN
    assert services.agent.session_work_dir == str(tmp_path / "state")
    assert services.agent.context_window == 32_000
    assert services.agent.instructions_content == "project instructions"
    assert not permissions.sandbox_enabled


@pytest.mark.parametrize("teams", [False, True])
def test_background_assembly_keeps_optional_tools_and_shared_managers(tmp_path, teams):
    agent = make_agent(tmp_path)
    provider = ProviderConfig("local", "anthropic", "https://invalid.example", "model")
    tasks, traces = TaskManager(), TraceManager()
    worktrees = WorktreeManager(str(tmp_path))
    services = assemble_background(agent, provider, worktrees, tasks, traces,
                                   BackgroundOptions(enable_fork=True, enable_teams=teams))
    assert agent.registry.get("Agent") is services.agent_tool
    assert services.agent_tool._task_manager is tasks
    assert services.agent_tool._trace_manager is traces
    assert services.agent_tool._team_manager is services.teams
    assert (agent.registry.get("TeamCreate") is not None) is teams
    assert (agent.registry.get("TeamDelete") is not None) is teams
    assert not tasks.has_active_tasks()


def test_session_controller_owns_reset_and_tool_history_binding(tmp_path):
    agent = make_agent(tmp_path)
    session = SessionManager(str(tmp_path)).create()
    try:
        agent._loop_count = 8
        agent._file_versions["old"] = object()
        agent.active_skills["old"] = "instructions"
        agent.total_input_tokens = 42
        SessionController().bind(session, agent)
        assert agent.session_id == session.session_id
        assert agent._loop_count == 0 and not agent._file_versions and not agent.active_skills
        assert agent.total_input_tokens == 42
        assert agent.file_history.session_id == session.session_id
        for tool in agent.registry.list_tools():
            if hasattr(tool, "file_history"):
                assert tool.file_history is agent.file_history
        agent.reset_usage()
        assert agent.total_input_tokens == agent.total_output_tokens == agent.usage_missing_requests == 0
    finally:
        session.close()


def test_session_is_published_before_authorization_callback_and_all_owned_tools_are_bound(tmp_path):
    agent = make_agent(tmp_path)
    frontend_registry = create_default_registry()
    session = SessionManager(str(tmp_path)).create()
    order = []
    def publish(bound):
        order.append(("published", bound.session_id))
    def save_authorization():
        assert order[-1] == ("published", session.session_id)
        order.append(("authorization", session.session_id))
    agent.approval_controller = SimpleNamespace(revision=0, persist_authorization=save_authorization)
    try:
        SessionController().bind(session, agent, registry=frontend_registry, on_bound=publish)
        assert order == [("published", session.session_id), ("authorization", session.session_id)]
        for tool in frontend_registry.list_tools():
            if hasattr(tool, "file_history"):
                assert tool.file_history is agent.file_history
    finally:
        session.close()


def test_failed_recovery_binding_does_not_publish_a_new_session(tmp_path):
    session = SessionManager(str(tmp_path)).create()
    publish = Mock()
    recovery = SimpleNamespace(bind_session=Mock(side_effect=OSError("recovery storage")))
    try:
        with pytest.raises(OSError, match="recovery storage"):
            SessionController(recovery).bind(session, None, on_bound=publish)
        publish.assert_not_called()
    finally:
        session.close()


class RecordSession:
    def __init__(self, error=None):
        self.error = error
        self.records = []
        self.messages = []
        self.meta = SimpleNamespace(total_tokens=0)

    def append_record(self, record):
        if self.error is not None and not isinstance(self.error, SessionMetadataError):
            raise self.error
        self.records.append(record)
        if self.error is not None:
            raise self.error

    def append(self, message):
        self.messages.append(message)


def candidate():
    conversation = ConversationManager()
    conversation.add_user_message("original")
    conversation.record_usage_anchor(1200)
    previous = deepcopy(conversation)
    conversation.replace_history([Message("user", "summary")])
    return conversation, previous


@pytest.mark.parametrize("kind", ["compact", "memory"])
@pytest.mark.parametrize("committed", [False, True])
def test_boundary_failure_distinguishes_body_from_metadata(kind, committed):
    conversation, previous = candidate()
    error = SessionMetadataError("metadata") if committed else OSError("body")
    session, notices = RecordSession(error), []
    persistence = SessionPersistence(session, conversation, notices.append, len(previous.history))
    if kind == "compact":
        event = CompactNotification(1200, "compact", CompactBoundary("summary", []), previous)
        commit = lambda: persistence.commit_compact(event, include_messages=True)
    else:
        event = MemoryContextChanged(previous)
        commit = lambda: persistence.commit_memory(event)
    if committed:
        commit()
        assert conversation.history[0].content == "summary"
        assert len(session.records) == 1 and "metadata" in notices[0]
        assert persistence.history_cursor == len(conversation.history)
    else:
        with pytest.raises(OSError, match="body"):
            commit()
        assert conversation.__dict__ == previous.__dict__ and not session.records


@pytest.mark.parametrize("kind", ["compact", "memory"])
def test_committed_boundary_cannot_be_rolled_back_by_a_diagnostic_failure(kind):
    conversation, previous = candidate()
    session = RecordSession(SessionMetadataError("metadata"))
    def fail_notice(text):
        raise RuntimeError("renderer unavailable")
    persistence = SessionPersistence(session, conversation, fail_notice)
    with pytest.raises(RuntimeError, match="renderer"):
        if kind == "compact":
            persistence.commit_compact(CompactNotification(1200, "compact", CompactBoundary("summary", []), previous))
        else:
            persistence.commit_memory(MemoryContextChanged(previous))
    assert conversation.history[0].content == "summary"
    assert len(session.records) == 1
    assert persistence.history_cursor == len(conversation.history)


def test_flush_retries_only_uncommitted_messages():
    conversation = ConversationManager([Message("user", "first"), Message("assistant", "second")])
    session = RecordSession()
    original = session.append
    def fail_second(message):
        if message.content == "second":
            raise OSError("storage")
        original(message)
    session.append = fail_second
    persistence = SessionPersistence(session, conversation, Mock())
    with pytest.raises(OSError):
        persistence.flush()
    assert persistence.history_cursor == 1
    session.append = original
    persistence.flush()
    persistence.flush()
    assert [message.content for message in session.messages] == ["first", "second"]


@pytest.mark.asyncio
async def test_foreground_run_persists_turns_before_presentation_and_finalizes_once():
    conversation = ConversationManager()
    session = RecordSession()
    observed = []
    closed = []
    class Actor:
        total_input_tokens = 20
        total_output_tokens = 5
        async def run(self, conv, *, interactive=True, source="user"):
            observed.append((interactive, source))
            try:
                conv.history.append(Message("assistant", "tool turn"))
                yield TurnComplete(1)
                conv.history.append(Message("assistant", "answer"))
                yield LoopComplete(2)
            finally:
                closed.append(True)
    persistence = SessionPersistence(session, conversation, Mock())
    async with aclosing(ForegroundRun(Actor(), persistence).events(conversation, interactive=False, source="notification")) as stream:
        first = await anext(stream)
        assert isinstance(first, TurnComplete) and len(session.messages) == 1
        second = await anext(stream)
        assert isinstance(second, LoopComplete) and len(session.messages) == 2
        assert session.meta.total_tokens == 25
    assert observed == [(False, "notification")] and closed == [True]
    assert len(session.messages) == 2


@pytest.mark.asyncio
async def test_early_stream_close_settles_actor_before_final_session_flush():
    conversation = ConversationManager()
    session = RecordSession()
    class Actor:
        async def run(self, conv, **kwargs):
            try:
                yield StreamText("partial")
                raise AssertionError("consumer must not request another event")
            finally:
                conv.history.append(Message("assistant", "settled result"))
    async with aclosing(ForegroundRun(Actor(), SessionPersistence(session, conversation, Mock())).events(conversation)) as stream:
        assert isinstance(await anext(stream), StreamText)
    assert [message.content for message in session.messages] == ["settled result"]


@pytest.mark.asyncio
async def test_cancellation_waits_for_actor_cleanup_and_persists_its_result():
    entered, released, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    conversation, session = ConversationManager(), RecordSession()
    class Actor:
        async def run(self, conv, **kwargs):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield StreamText("unreachable")
            finally:
                await released.wait()
                conv.history.append(Message("assistant", "cancelled tool result"))
                settled.set()
    async def consume():
        async with aclosing(ForegroundRun(Actor(), SessionPersistence(session, conversation, Mock())).events(conversation)) as stream:
            async for event in stream:
                pass
    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not session.messages
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert settled.is_set() and session.messages[-1].content == "cancelled tool result"


def test_factory_preserves_share_copy_and_no_nested_spawn_rules(tmp_path):
    parent = make_agent(tmp_path)
    parent.trace_id = "trace"
    parent.hook_engine = object()
    registry = ToolRegistry()
    permissions = AgentFactory.permissions(parent, str(tmp_path), PermissionMode.PLAN)
    context = SpawnContext.inherit(parent, client=parent.client, registry=registry,
                                  work_dir=str(tmp_path), permissions=permissions, instructions="child",
                                  max_iterations=4, sandbox_root=str(tmp_path), fork=True)
    child = AgentFactory.create(context)
    assert child.client is parent.client and child.registry is registry
    assert child.hook_engine is parent.hook_engine and child.permission_checker is permissions
    assert child.parent_id == parent.agent_id and child.trace_id == "trace"
    assert child.replacement_state is not parent.replacement_state
    assert not child.spawn_allowed and child.permission_mode is PermissionMode.PLAN
    assert child.max_iterations == 4 and child.sandbox_root == str(tmp_path)


class DisplayOnly:
    def __init__(self):
        self.messages = []

    def add_system_message(self, text):
        self.messages.append(text)

    def refresh_status(self):
        pass


@pytest.mark.asyncio
async def test_clear_and_session_commands_use_explicit_ports_not_ui_state(tmp_path):
    manager, agent, ui = SessionManager(str(tmp_path)), make_agent(tmp_path), DisplayOnly()
    session = manager.create()
    bound, conversations = [], []
    services = CommandServices(sessions=SessionActions(
        set_session=bound.append, set_conversation=conversations.append,
        clear_chat=lambda: None,
    ))
    context = CommandContext("", agent, ConversationManager(), session, manager, None, ui, services)
    try:
        await handle_clear(context)
        assert len(bound) == len(conversations) == 1
        context.session = bound[-1]
        context.args = "new"
        await handle_session(context)
        assert len(bound) == len(conversations) == 2
        assert context.services is services
    finally:
        session.close()
        for item in bound:
            item.close()
