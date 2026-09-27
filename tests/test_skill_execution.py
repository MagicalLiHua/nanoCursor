"""Skill contracts through real parsers, agents, registries and UI entrypoints."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from mcp import types as mcp_types

from nanocursor.client import LLMClient
from nanocursor.config import ProviderConfig
from nanocursor.conversation import ConversationManager, Message
from nanocursor.hooks import HookEngine, load_hooks
from nanocursor.mcp.tool_wrapper import MCPToolWrapper
from nanocursor.permissions import PermissionMode, RuleEngine
from nanocursor.skills.executor import SkillExecutor
from nanocursor.skills.loader import SkillLoader
from nanocursor.skills.parser import SkillDef, SkillParseError, parse_skill_file
from nanocursor.skills.runtime import build_scope, resolve_provider
from nanocursor.tools import create_default_registry
from nanocursor.tools.base import StreamEnd, TextDelta, ToolCallComplete
from nanocursor.tools.impl.tool_search import ToolSearchTool, ToolSearchParams
from nanocursor.tools.load_skill import LoadSkill, LoadSkillParams
from test_execution_boundaries import agent, checker


def definition(**kwargs):
    return SkillDef(name="review", description="Review changes", prompt_body="REVIEW $ARGUMENTS", mode="fork", context="none", **kwargs)


def save_skill(root, meta, body="Do the task", format="md"):
    root.mkdir(parents=True, exist_ok=True)
    if format == "yaml":
        path = root / "skill.yaml"
        path.write_text(yaml.safe_dump(meta))
        (root / "prompt.md").write_text(body)
    else:
        path = root / ("SKILL.md" if format == "dir" else "review.md")
        path.write_text("---\n" + yaml.safe_dump(meta) + "---\n" + body)
    return path


@pytest.mark.parametrize("format", ["md", "dir", "yaml"])
@pytest.mark.parametrize("settings", [
    {"mode": []}, {"context": 3}, {"model": False}, {"provider": ""},
    {"tools": "Bash"}, {"tools": None}, {"tools": ["Bash(*)"]}, {"tools": [""]},
    {"tools": [1]}, {"allowed-tools": ["Bash"]}, {"endpoint": "https://invalid"},
    {"description": []}, {"mode": "inline", "context": "recent"},
    {"mode": "inline", "model": "other"}, {"mode": "inline", "tools": []},
])
def test_formats_reject_the_same_invalid_declarations(tmp_path, format, settings):
    path = save_skill(tmp_path, {"name": "review", "description": "review", "mode": "fork", **settings}, format=format)
    with pytest.raises(SkillParseError):
        parse_skill_file(path)


@pytest.mark.parametrize("format", ["md", "dir", "yaml"])
def test_formats_share_valid_contract(tmp_path, format):
    path = save_skill(tmp_path, {"name": "review", "description": "contains --- text", "mode": "fork", "provider": "reviewer",
                               "tools": ["ReadFile", "ReadFile"], "metadata": {"custom": "kept"}}, format=format)
    skill = parse_skill_file(path)
    assert skill.tools == ("ReadFile",) and skill.provider == "reviewer"
    assert skill.metadata["metadata"]["custom"] == "kept"


def test_yaml_reload_prompt_config_delete_and_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    directory = root / ".nanocursor/skills/review"
    path = save_skill(directory, {"mode": "fork", "description": "review"}, body="v1", format="yaml")
    save_skill(tmp_path / "home/skills", {"name": "review", "description": "user fallback"})
    loader = SkillLoader(str(root))
    assert loader.load_all()["review"].prompt_body == "v1"
    (directory / "prompt.md").write_text("v2")
    assert loader.needs_reload() and loader.get("review").prompt_body == "v2"
    path.write_text("name: review\nmode: wrong\ndescription: x")
    assert loader.get("review") is None and not loader.get_catalog()
    assert str(path) in loader.diagnostics["review"]
    path.write_text("name: review\nmode: fork\ndescription: x\ntools: []")
    assert loader.get("review").tools == ()
    (directory / "prompt.md").unlink()
    assert loader.get("review") is None


def profiles():
    return [ProviderConfig("main", "openai-compat", "https://main.invalid/v1", "main-model", auth="none", context_window=200000),
            ProviderConfig("reviewer", "openai", "https://review.invalid/v1", "review-model", auth="none", context_window=32000, max_output_tokens=1000, thinking=True)]


def test_exact_provider_model_routing_and_invalid_profiles():
    main, review = profiles()
    assert resolve_provider(definition(provider="reviewer"), [main, review], main) == review
    assert resolve_provider(definition(model="review-model"), [main, review], main) == review
    duplicate = copy.deepcopy(review)
    duplicate.name = "duplicate"
    for skill in (definition(model="haiku"), definition(provider="missing"),
                  definition(provider="main", model="review-model")):
        with pytest.raises(ValueError):
            resolve_provider(skill, [main, review], main)
    with pytest.raises(ValueError, match="disambiguate"):
        resolve_provider(definition(model="review-model"), [main, review, duplicate], main)
    assert resolve_provider(definition(model="review-model"), [main, review, duplicate], review).name == "reviewer"
    review._needs_trust = True
    with pytest.raises(ValueError, match="approval"):
        resolve_provider(definition(provider="reviewer"), [main, review], main)


class Capture(LLMClient):
    max_output_tokens = 1000

    def __init__(self, calls=(), terminal=True, hold=None, missing_usage=False):
        self.calls, self.terminal, self.hold = calls, terminal, hold
        self.missing_usage = missing_usage
        self.requests, self.closed = [], False
        self.entered = asyncio.Event()
        self.stream_closed = False

    async def stream(self, conversation, system="", tools=None, **kwargs):
        self.requests.append((copy.deepcopy(conversation.history), tools, system))
        self.entered.set()
        try:
            if self.hold:
                await self.hold.wait()
            if len(self.requests) == 1:
                for call in self.calls:
                    yield call
            yield TextDelta("RESULT")
            if self.terminal:
                yield StreamEnd("end_turn", input_tokens=42, output_tokens=7, usage_available=not self.missing_usage)
        finally:
            self.stream_closed = True

    async def aclose(self):
        self.closed = True


def executor(tmp_path, monkeypatch, child=None, mode=PermissionMode.DEFAULT):
    parent_client, child = Capture(), child or Capture()
    main, review = profiles()
    parent = agent(tmp_path, parent_client, create_default_registry(), permission_checker=checker(tmp_path, mode))
    selected = []
    def factory(config):
        selected.append(copy.deepcopy(config))
        return child
    monkeypatch.setattr("nanocursor.skills.executor.create_client", factory)
    return SkillExecutor(parent, parent_client, main.protocol, providers=[main, review], current_provider=main), child, selected


@pytest.mark.asyncio
async def test_independent_routing_and_parent_state(tmp_path, monkeypatch):
    exe, child, selected = executor(tmp_path, monkeypatch)
    before = exe.agent.recovery_state.snapshot_skills()
    result = await exe.execute_fork(definition(provider="reviewer", tools=()), "args")
    assert result.status == "success" and result.model == "review-model"
    assert result.input_tokens == 42 and result.output_tokens == 7
    assert selected[0].protocol == "openai" and selected[0].thinking
    assert selected[0].get_context_window() == 32000 and selected[0].max_output_tokens == 1000
    assert child.requests[0][1] == [] and child.closed
    assert "REVIEW args" in "\n".join(m.content for m in child.requests[0][0])
    assert not exe.client.requests and not exe.client.closed
    assert exe.agent.recovery_state.snapshot_skills() == before and not exe.agent.active_skills
    assert exe.agent.total_input_tokens == 0 and exe.agent.registry.is_enabled("Bash")


@pytest.mark.asyncio
@pytest.mark.parametrize("tools", [(), ("ReadFile",)])
async def test_forged_write_and_system_tools_cannot_run_hooks(tmp_path, monkeypatch, tools):
    marker = tmp_path / "forged.txt"
    client = Capture([ToolCallComplete("1", "WriteFile", {"file_path": str(marker), "content": "bad"}),
                      ToolCallComplete("2", "LoadSkill", {"name": "nested"})])
    exe, _, _ = executor(tmp_path, monkeypatch, client)
    hook = tmp_path / "hook"
    exe.agent.hook_engine = HookEngine(load_hooks([{"event": "pre_tool_use", "action": {"type": "command", "command": f"touch '{hook}'"}}]))
    result = await exe.execute_fork(definition(tools=tools), "")
    assert result.status == "success" and not marker.exists() and not hook.exists()
    results = [r for m in client.requests[-1][0] for r in m.tool_results]
    assert len(results) == 2 and all(r.is_error for r in results)
    assert not exe.agent.hook_engine._tasks


@pytest.mark.parametrize("tools", [("Agent",), ("LoadSkill",), ("Bash",), ("unknown",)])
def test_unknown_disabled_and_control_tools_fail_before_request(tmp_path, monkeypatch, tools):
    exe, client, selected = executor(tmp_path, monkeypatch)
    exe.agent.registry.disable("Bash")
    with pytest.raises(ValueError, match="unsupported"):
        exe.prepare_fork(definition(tools=tools), "")
    assert not selected and not client.requests


@pytest.mark.asyncio
async def test_scoped_mcp_search_direct_schema_disable_and_reconnect(tmp_path):
    parent = agent(tmp_path, registry=create_default_registry())
    transport = NS(is_alive=True, _session=object(), call_tool=AsyncMock())
    for name in ("allowed", "hidden"):
        parent.registry.register(MCPToolWrapper("server", mcp_types.Tool(name=name, description="review", inputSchema={"type": "object"}), transport))
    parent.registry.register(ToolSearchTool(parent.registry))
    scope = build_scope(definition(tools=("ToolSearch", "mcp_server_allowed")), parent, "openai")
    assert scope.registry.get_deferred_tool_names() == ["mcp_server_allowed"]
    search = scope.registry.get("ToolSearch")
    for query in ("review", "select:mcp_server_allowed,mcp_server_hidden"):
        result = await search.execute(ToolSearchParams(query=query))
        assert '"name": "mcp_server_allowed"' in result.output and '"name": "mcp_server_hidden"' not in result.output
    assert not parent.registry.is_discovered("mcp_server_allowed")
    direct = build_scope(definition(tools=("mcp_server_allowed",)), parent, "anthropic")
    assert [s["name"] for s in direct.registry.get_all_schemas()] == ["mcp_server_allowed"]
    parent.registry.disable("mcp_server_allowed")
    assert not scope.registry.find_deferred_by_names(["mcp_server_allowed"])
    assert not scope.registry.is_enabled("mcp_server_allowed")
    parent.registry.enable_all()
    transport._session = object()
    assert not scope.registry.is_enabled("mcp_server_allowed")


@pytest.mark.parametrize("change", ["mode", "rules", "cwd", "allowed", "sandbox"])
def test_authority_change_invalidates_snapshot(tmp_path, monkeypatch, change):
    exe, _, _ = executor(tmp_path, monkeypatch)
    rules = tmp_path / "rules.yaml"
    rules.write_text("[]")
    exe.agent.permission_checker.rule_engine = RuleEngine(project_rules_path=rules)
    invocation = exe.prepare_fork(definition(tools=("WriteFile",)), "")
    if change == "mode":
        exe.agent.set_permission_mode(PermissionMode.BYPASS)
    elif change == "rules":
        rules.write_text('- rule: "WriteFile(*)"\n  effect: allow')
    elif change == "cwd":
        exe.agent.work_dir = str(tmp_path / "other")
    elif change == "allowed":
        exe.agent.permission_checker.add_session_allow("WriteFile", "*")
    else:
        exe.agent.permission_checker.sandbox._allowed_roots.append(Path("/"))
    assert invocation.scope.guard()
    assert not invocation.scope.permission_checker._session_allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,effect,allowed", [
    (PermissionMode.DEFAULT, None, False), (PermissionMode.ACCEPT_EDITS, None, True),
    (PermissionMode.ACCEPT_EDITS, "deny", False), (PermissionMode.ACCEPT_EDITS, "ask", False),
    (PermissionMode.PLAN, None, False),
])
async def test_fork_inherits_permissions(tmp_path, monkeypatch, mode, effect, allowed):
    target = tmp_path / "created.txt"
    client = Capture([ToolCallComplete("write", "WriteFile", {"file_path": str(target), "content": "safe"})])
    exe, _, _ = executor(tmp_path, monkeypatch, client, mode)
    if effect:
        rules = tmp_path / "rules.yaml"
        rules.write_text(f'- rule: "WriteFile(*)"\n  effect: {effect}')
        exe.agent.permission_checker.rule_engine = RuleEngine(project_rules_path=rules)
    before = copy.deepcopy(exe.agent.permission_checker._session_allowed)
    await exe.execute_fork(definition(tools=("WriteFile",)), "")
    assert target.exists() is allowed
    assert exe.agent.permission_checker._session_allowed == before
    if not allowed:
        assert any(r.is_error for m in client.requests[-1][0] for r in m.tool_results)


@pytest.mark.asyncio
async def test_load_skill_uses_fork_and_args_without_parent_activation(tmp_path, monkeypatch):
    exe, child, _ = executor(tmp_path, monkeypatch)
    exe.agent._current_conversation = ConversationManager([Message("user", "CURRENT")])
    skill = definition(provider="reviewer", tools=())
    skill.context = "recent"
    tool = LoadSkill()
    tool.set_loader(NS(get=lambda _: skill))
    tool.set_agent(exe.agent)
    tool.set_executor(exe)
    result = await tool.execute(LoadSkillParams(name="review", args="PATCH"))
    assert not result.is_error and "review-model" in result.output
    assert any("CURRENT" in m.content for m in child.requests[0][0])
    assert any("REVIEW PATCH" in m.content for m in child.requests[0][0])
    assert not exe.agent.active_skills


@pytest.mark.asyncio
async def test_failure_usage_and_cancellation_cleanup(tmp_path, monkeypatch):
    exe, child, _ = executor(tmp_path, monkeypatch, Capture(terminal=False))
    result = await exe.execute_fork(definition(), "")
    assert result.status == "error" and child.closed and result.usage_unknown
    exe, child, _ = executor(tmp_path, monkeypatch, Capture(missing_usage=True))
    assert (await exe.execute_fork(definition(), "")).usage_unknown
    exe, child, _ = executor(tmp_path, monkeypatch, Capture(hold=asyncio.Event()))
    task = asyncio.create_task(exe.execute_fork(definition(), ""))
    await child.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child.closed and child.stream_closed and not exe.client.closed
    assert exe.last_result.status == "cancelled"


@pytest.mark.asyncio
async def test_two_invocations_capture_configuration_and_do_not_share_hooks(tmp_path, monkeypatch):
    exe, _, _ = executor(tmp_path, monkeypatch)
    clients = []
    def factory(_):
        client = Capture()
        clients.append(client)
        return client
    monkeypatch.setattr("nanocursor.skills.executor.create_client", factory)
    exe.agent.hook_engine = HookEngine(load_hooks([{"id": "once", "event": "pre_send", "once": True,
                                                  "action": {"type": "prompt", "message": "CHILD HOOK"}}]))
    skill = definition(tools=())
    first, second = exe.prepare_fork(skill, "FIRST"), exe.prepare_fork(skill, "SECOND")
    skill.prompt_body = "MUTATED"
    results = await asyncio.gather(exe.execute_fork(skill, "", invocation=first), exe.execute_fork(skill, "", invocation=second))
    assert all(r.status == "success" for r in results)
    assert all("MUTATED" not in "\n".join(m.content for m in c.requests[0][0]) for c in clients)
    assert all("CHILD HOOK" in c.requests[0][2] for c in clients)
    assert not exe.agent.hook_engine.get_prompt_messages() and not exe.agent.hook_engine._closing


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["anthropic", "openai", "openai-compat"])
async def test_fork_uses_selected_real_protocol_adapter(tmp_path, monkeypatch, protocol):
    """Exercise SDK request serialization, not just a captured ProviderConfig."""
    from test_stream_usage import chunk, usage
    requests, constructor = [], []
    class Events:
        async def __aiter__(self):
            if protocol == "anthropic":
                yield NS(type="content_block_delta", delta=NS(type="text_delta", text="WIRE_RESULT"))
            elif protocol == "openai":
                yield NS(type="response.output_text.delta", delta="WIRE_RESULT")
                yield NS(type="response.completed", response=NS(status="completed", output=[],
                    usage=NS(input_tokens=42, output_tokens=7, input_tokens_details=NS(cached_tokens=0))))
            else:
                yield chunk(content="WIRE_RESULT", finish="stop", usage=usage(prompt=42, output=7))
        async def get_final_message(self):
            return NS(stop_reason="end_turn", usage=NS(input_tokens=42, output_tokens=7))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def close(self):
            pass
    def sync_request(**kwargs):
        requests.append(kwargs)
        return Events()
    async def request(**kwargs):
        return sync_request(**kwargs)
    sdk = NS(messages=NS(stream=sync_request), responses=NS(create=request),
             chat=NS(completions=NS(create=request)), close=AsyncMock())
    def create(**kwargs):
        constructor.append(kwargs)
        return sdk
    monkeypatch.setattr("nanocursor.client.AsyncAnthropic" if protocol == "anthropic" else "nanocursor.client.AsyncOpenAI", create)
    main, review = profiles()
    review.protocol, review.thinking = protocol, False
    parent = agent(tmp_path, Capture(), create_default_registry(), permission_checker=checker(tmp_path))
    exe = SkillExecutor(parent, parent.client, main.protocol, providers=[main, review], current_provider=main)
    result = await exe.execute_fork(definition(provider="reviewer", tools=("ReadFile",)), "target")
    assert result.status == "success", result
    assert result.text == "WIRE_RESULT" and result.input_tokens == 42 and result.output_tokens == 7
    assert constructor[0]["base_url"] == review.base_url
    assert requests[0]["model"] == review.model
    assert requests[0]["max_output_tokens" if protocol == "openai" else "max_tokens"] == 1000
    if protocol == "openai":
        assert "input" in requests[0] and requests[0]["tools"][0]["name"] == "ReadFile"
    elif protocol == "openai-compat":
        assert requests[0]["tools"][0]["function"]["name"] == "ReadFile"
    else:
        assert requests[0]["tools"][0]["name"] == "ReadFile"
    sdk.close.assert_awaited_once()
    assert not parent.client.requests


def test_optional_recall_client_disables_sdk_retries(monkeypatch):
    from nanocursor.client import create_client
    for protocol in ("anthropic", "openai", "openai-compat"):
        factory = Mock()
        monkeypatch.setattr("nanocursor.client.AsyncAnthropic" if protocol == "anthropic" else "nanocursor.client.AsyncOpenAI", factory)
        config = ProviderConfig("recall", protocol, "https://invalid", "model", auth="none")
        create_client(config, max_retries=0)
        assert factory.call_args.kwargs["max_retries"] == 0


@pytest.mark.asyncio
async def test_parent_mode_change_while_child_hook_waits_blocks_write(tmp_path, monkeypatch):
    path = tmp_path / "should-not-write"
    client = Capture([ToolCallComplete("w", "WriteFile", {"file_path": str(path), "content": "no"})])
    exe, _, _ = executor(tmp_path, monkeypatch, client, PermissionMode.ACCEPT_EDITS)
    exe.agent.hook_engine = HookEngine(load_hooks([{"event": "pre_tool_use", "action": {"type": "prompt", "message": "wait"}}]))
    entered, release = asyncio.Event(), asyncio.Event()
    async def wait_hook(self, context):
        entered.set()
        await release.wait()
        return None
    monkeypatch.setattr(HookEngine, "run_pre_tool_hooks", wait_hook)
    task = asyncio.create_task(exe.execute_fork(definition(tools=("WriteFile",)), ""))
    await entered.wait()
    exe.agent.set_permission_mode(PermissionMode.BYPASS)
    release.set()
    result = await task
    assert result.status == "error" and not path.exists()
    assert not exe.agent.hook_engine._closing


@pytest.mark.asyncio
async def test_target_context_is_bounded_and_long_sop_fails_without_request(tmp_path, monkeypatch):
    from nanocursor.memory.budget import estimate
    exe, client, _ = executor(tmp_path, monkeypatch)
    skill = definition(provider="reviewer", tools=())
    skill.context = "recent"
    snapshot = [Message("user", "HUGE_CONTEXT " * 10000)]
    result = await exe.execute_fork(skill, "task", context_messages=snapshot)
    assert result.status == "success"
    inherited = [m.content for m in client.requests[0][0] if "HUGE_CONTEXT" in m.content]
    assert inherited and sum(map(estimate, inherited)) <= 8192
    assert "[truncated]" in inherited[0]
    before = len(client.requests)
    skill.prompt_body = "TOO_LONG " * 100000
    assert (await exe.execute_fork(skill, "", context_messages=snapshot)).status == "error"
    assert len(client.requests) == before


def test_missing_credential_fails_without_sdk_construction(tmp_path, monkeypatch):
    exe, client, selected = executor(tmp_path, monkeypatch)
    exe.providers[1].auth = "key"
    exe.providers[1].api_key_env = "NANOCURSOR_TEST_MISSING_SKILL_KEY"
    monkeypatch.delenv("NANOCURSOR_TEST_MISSING_SKILL_KEY", raising=False)
    with pytest.raises(ValueError, match="No credential"):
        exe.prepare_fork(definition(provider="reviewer"), "")
    assert not selected and not client.requests
