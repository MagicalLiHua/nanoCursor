from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import yaml
from mcp.types import CallToolResult, TextContent, Tool as MCPTool
from rich.text import Text

from nanocursor.agent import Agent, PermissionResponse
from nanocursor.config import MCPServerConfig, load_config
from nanocursor.mcp.client import MCPClient
from nanocursor.mcp.manager import MCPManager
from nanocursor.mcp.settings import MCPSettings
from nanocursor.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from nanocursor.tools import create_default_registry
from nanocursor.tools.base import ToolCallComplete
from nanocursor.tools.manage_mcp import ManageMCP, ManageMCPParams


PROFILE = {"name": "demo", "protocol": "openai-compat", "base_url": "http://localhost",
           "model": "offline", "auth": "none"}


class FakeClient:
    def __init__(self, config):
        self.config = config
        self.is_alive = False
        self.instructions = f"Instructions for {config.name}"
        self.closed = False
        self.work_dir = None
        self.entered = asyncio.Event()
        self.hold = None
        self.fail_list = False
        self.definitions = [MCPTool(name="search", description="Search docs", inputSchema={"type": "object"})]

    async def connect(self):
        self.entered.set()
        if self.hold:
            await self.hold.wait()
        self.is_alive = True

    async def list_tools(self):
        if self.fail_list:
            raise RuntimeError("tool discovery failed")
        return self.definitions

    async def call_tool(self, name, args):
        return CallToolResult(content=[TextContent(type="text", text="MCP RESULT")])

    async def close(self):
        self.is_alive = False
        self.closed = True


@pytest.fixture
def managed(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("NANOCURSOR_HOME", str(home))
    raw = {"schema_version": 2, "providers": [PROFILE], "default_provider": "demo",
           "approval": {"mode": "manual"}, "custom_setting": {"keep": True}}
    (home / "config.yaml").write_text(yaml.safe_dump(raw))
    registry = create_default_registry()
    manager = MCPManager(work_dir=str(project))
    checker = PermissionChecker(DangerousCommandDetector(), PathSandbox(str(project)), RuleEngine(),
                                mode=PermissionMode.BYPASS)
    agent = Agent(Mock(), registry, "openai-compat", str(project), permission_checker=checker)
    tool = ManageMCP(manager, registry, owner_id=agent.agent_id, workspace=project)
    registry.register(tool)
    clients = []
    def create(config):
        client = FakeClient(config)
        clients.append(client)
        return client
    monkeypatch.setattr("nanocursor.mcp.manager.MCPClient", create)
    return NS(project=project, home=home, original=raw, registry=registry, manager=manager,
              agent=agent, tool=tool, clients=clients, create=create)


async def run(env, action, *, approval=None, **arguments):
    call = ToolCallComplete("manage", "ManageMCP", {"action": action, **arguments})
    result = await env.agent._execute_single_tool_direct(call, approval)
    return result.result


def read_saved(env):
    return yaml.safe_load((env.home / "config.yaml").read_text())


@pytest.mark.asyncio
async def test_start_stop_and_restart_save_configuration_and_revoke_old_tools(managed):
    env = managed
    result = await run(env, "start", name="docs", config={"url": "https://docs.example/mcp"})
    assert not result.is_error, result.output
    saved = read_saved(env)
    assert {key: saved[key] for key in env.original} == env.original
    assert saved["mcp_servers"][0]["enabled"] is True
    assert (env.home / "config.yaml").stat().st_mode & 0o777 == 0o600
    old = env.registry.get("mcp_docs_search")
    env.registry.mark_discovered(old.name)
    assert any(schema["name"] == old.name for schema in env.registry.get_all_schemas())
    result = await run(env, "stop", name="docs")
    assert not result.is_error
    assert env.clients[0].closed
    assert env.registry.get(old.name) is None
    assert not env.registry.is_discovered(old.name)
    assert not env.registry.find_deferred_by_names([old.name])
    stale = await old.execute(old.validate_arguments({}))
    assert stale.is_error and not env.clients[0].is_alive
    assert await env.manager.get_client("docs") is None
    assert read_saved(env)["mcp_servers"][0]["enabled"] is False
    assert load_config(env.home / "config.yaml").mcp_servers == []
    result = await run(env, "start", name="docs")
    assert not result.is_error
    assert env.clients[1] is not env.clients[0] and env.clients[1].is_alive
    assert env.registry.get(old.name) is not old
    assert old.name in env.registry.get_deferred_tool_names()
    assert "docs" in env.manager.instructions()
    await env.manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS])
async def test_mutations_require_approval_and_list_does_not(managed, mode):
    env = managed
    env.agent.set_permission_mode(mode)
    before = (env.home / "config.yaml").read_bytes()
    prompts = []
    async def deny(prompt):
        prompts.append(prompt)
        return PermissionResponse.DENY
    result = await run(env, "start", name="local", config={"command": "npx", "args": ["-y", "example"]}, approval=deny)
    assert result.is_error and len(prompts) == 1
    assert prompts[0].allow_always is False
    assert prompts[0].arguments["config"]["args"] == ["-y", "example"]
    assert not env.clients and (env.home / "config.yaml").read_bytes() == before
    assert not (await run(env, "list")).is_error
    result = await run(env, "start", name="local", config={"command": "npx"})
    assert result.is_error and not env.clients


@pytest.mark.asyncio
async def test_plan_and_explicit_deny_cannot_manage_servers(managed):
    env = managed
    env.agent.set_permission_mode(PermissionMode.PLAN)
    assert (await run(env, "start", name="docs", config={"url": "https://docs.example/mcp"})).is_error
    assert not env.clients
    rules = env.project / "rules.yaml"
    rules.write_text('- rule: "ManageMCP(*)"\n  effect: deny\n')
    env.agent.permission_checker.rule_engine = RuleEngine(user_rules_path=rules)
    env.agent.set_permission_mode(PermissionMode.BYPASS)
    assert (await run(env, "list")).is_error
    assert (await run(env, "start", name="docs", config={"url": "https://docs.example/mcp"})).is_error
    assert not env.clients


@pytest.mark.asyncio
async def test_saved_config_is_bound_before_approval_and_concurrent_edits_are_preserved(managed):
    env = managed
    await run(env, "start", name="docs", config={"command": "old-command"})
    await run(env, "stop", name="docs")
    env.agent.set_permission_mode(PermissionMode.DEFAULT)
    async def changed(prompt):
        assert prompt.arguments["config"]["command"] == "old-command"
        raw = read_saved(env)
        raw["custom_setting"]["edited"] = True
        (env.home / "config.yaml").write_text(yaml.safe_dump(raw))
        return PermissionResponse.ALLOW
    result = await run(env, "start", name="docs", approval=changed)
    assert result.is_error and "changed" in result.output
    assert len(env.clients) == 1 and read_saved(env)["custom_setting"]["edited"] is True


@pytest.mark.asyncio
async def test_failure_and_cancellation_leave_disabled_and_release_client(managed, monkeypatch):
    env = managed
    def failing(config):
        client = env.create(config)
        client.fail_list = True
        return client
    monkeypatch.setattr("nanocursor.mcp.manager.MCPClient", failing)
    result = await run(env, "start", name="docs", config={"url": "https://docs.example/mcp"})
    assert result.is_error and env.clients[-1].closed
    assert not env.registry.get("mcp_docs_search")
    assert read_saved(env)["mcp_servers"][0]["enabled"] is False
    entered = asyncio.Event()
    def blocking(config):
        client = env.create(config)
        client.entered = entered
        client.hold = asyncio.Event()
        return client
    monkeypatch.setattr("nanocursor.mcp.manager.MCPClient", blocking)
    task = asyncio.create_task(run(env, "start", name="docs"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert env.clients[-1].closed and not env.manager._clients
    assert read_saved(env)["mcp_servers"][0]["enabled"] is False
    env.manager.connect_timeout = 0.02
    result = await run(env, "start", name="docs")
    assert result.is_error and env.clients[-1].closed


@pytest.mark.asyncio
async def test_global_edit_does_not_silently_override_project_configuration(managed):
    env = managed
    config_dir = env.project / ".nanocursor"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.yaml").write_text('mcp_servers:\n  - name: docs\n    enabled: false\n')
    before = (env.home / "config.yaml").read_bytes()
    result = await run(env, "start", name="docs", config={"command": "unused"})
    assert result.is_error and "Project configuration overrides" in result.output
    assert not env.clients and (env.home / "config.yaml").read_bytes() == before


@pytest.mark.asyncio
async def test_child_cannot_manage_parent_even_with_shared_tool(managed):
    env = managed
    original = env.agent.agent_id
    env.agent.agent_id = "another-agent"
    result = await run(env, "start", name="docs", config={"command": "unused"})
    assert result.is_error and "owning main session" in result.output
    assert not env.clients and "mcp_servers" not in read_saved(env)
    env.agent.agent_id = original


@pytest.mark.asyncio
async def test_tool_name_collision_keeps_existing_service(managed, monkeypatch):
    env = managed
    def create(config):
        client = env.create(config)
        name = "c_search" if config.name == "a" else "search"
        client.definitions = [MCPTool(name=name, inputSchema={"type": "object"})]
        return client
    monkeypatch.setattr("nanocursor.mcp.manager.MCPClient", create)
    assert not (await run(env, "start", name="a", config={"command": "unused"})).is_error
    original = env.registry.get("mcp_a_c_search")
    result = await run(env, "start", name="a_c", config={"command": "unused"})
    assert result.is_error and "collision" in result.output
    assert env.registry.get(original.name) is original and env.clients[0].is_alive
    assert env.clients[1].closed
    await env.manager.shutdown()


@pytest.mark.parametrize("config", [
    {"command": "x", "url": "https://example.com"}, {"command": ""}, {"url": ""},
    {"url": "file:///tmp/file"}, {"url": "https://key:secret@example.com"},
    {"command": "x", "args": "-y"}, {"command": "x", "typo": True},
])
def test_invalid_configuration_rejected_before_approval(config):
    with pytest.raises(ValueError):
        ManageMCPParams(action="start", name="demo", config=config)


@pytest.mark.asyncio
async def test_tools_command_lists_actual_enabled_and_deferred_state(managed):
    from nanocursor.commands.handlers.tools import handle_tools
    from nanocursor.commands.registry import CommandContext
    env = managed
    await run(env, "start", name="docs", config={"url": "https://docs.example/mcp"})
    env.registry.disable("Bash")
    messages = []
    ctx = CommandContext("", env.agent, None, None, None, None, NS(add_system_message=messages.append), {})
    await handle_tools(ctx)
    output = Text.from_markup(messages[-1]).plain
    assert "Bash  [内置] 已禁用" in output
    assert "mcp_docs_search  [MCP: docs] 已启用 · 待发现" in output
    ctx.args = "enabled"
    await handle_tools(ctx)
    assert "Bash  [" not in Text.from_markup(messages[-1]).plain
    env.registry.mark_discovered("mcp_docs_search")
    await handle_tools(ctx)
    assert "mcp_docs_search  [MCP: docs] 已启用 · 已提供" in Text.from_markup(messages[-1]).plain
    ctx.args = "disabled"
    await handle_tools(ctx)
    assert "Bash  [" in Text.from_markup(messages[-1]).plain and "mcp_docs_search" not in messages[-1]
    await env.manager.shutdown()


@pytest.mark.asyncio
async def test_real_stdio_connection_can_start_call_and_stop_from_different_tasks(tmp_path):
    script = tmp_path / "server.py"
    script.write_text('''import os
from mcp.server.fastmcp import FastMCP
server = FastMCP("local-test")
@server.tool()
def ping() -> str:
    return str(os.getpid()) + ":" + os.getcwd()
server.run(transport="stdio")
''')
    registry = create_default_registry()
    manager = MCPManager(work_dir=str(tmp_path))
    config = MCPServerConfig("local", command=sys.executable, args=[str(script)])
    try:
        result = await asyncio.wait_for(asyncio.create_task(manager.start(config, registry)), 15)
        assert len(result.tools) == 1
        tool = registry.get("mcp_local_ping")
        owner = tool._client._owner
        response = await asyncio.create_task(tool.execute(tool.validate_arguments({})))
        assert not response.is_error, response.output
        pid, cwd = response.output.split(":", 1)
        assert Path(cwd).resolve() == tmp_path.resolve()
        await asyncio.create_task(manager.stop("local"))
        assert owner.done() and not registry.get(tool.name)
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid), 0)
        assert (await tool.execute(tool.validate_arguments({}))).is_error
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_real_stdio_cancel_during_handshake_terminates_child(tmp_path):
    marker = tmp_path / "pid"
    script = tmp_path / "hang.py"
    script.write_text("import os, sys, time\nopen(sys.argv[1], 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    client = MCPClient(MCPServerConfig("hang", command=sys.executable, args=[str(script), str(marker)]))
    task = asyncio.create_task(client.connect())
    try:
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert not client.is_alive
        assert client._owner is None or client._owner.done()
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.close()


@pytest.mark.asyncio
async def test_http_transport_discovers_and_calls_tools_with_environment_header(tmp_path, monkeypatch):
    import httpx
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("http-test", stateless_http=True, json_response=True)
    @server.tool()
    def ping() -> str:
        return "HTTP RESULT"
    app = server.streamable_http_app()
    received = []
    async def capture(request):
        received.append(request.headers.get("authorization"))
    base_client = httpx.AsyncClient
    def create_client(**kwargs):
        return base_client(**kwargs, transport=httpx.ASGITransport(app=app), event_hooks={"request": [capture]})
    monkeypatch.setattr("nanocursor.mcp.client.httpx.AsyncClient", create_client)
    monkeypatch.setenv("MCP_TEST_TOKEN", "synthetic-token")
    manager = MCPManager()
    registry = create_default_registry()
    config = MCPServerConfig("http", url="http://127.0.0.1:8000/mcp", headers={"Authorization": "Bearer ${MCP_TEST_TOKEN}"})
    async with app.router.lifespan_context(app):
        try:
            result = await asyncio.wait_for(manager.start(config, registry), 5)
            tool = result.tools[0]
            response = await tool.execute(tool.validate_arguments({}))
            assert not response.is_error and response.output == "HTTP RESULT"
            assert received and all(value == "Bearer synthetic-token" for value in received)
        finally:
            await manager.shutdown()


@pytest.mark.asyncio
async def test_config_write_failure_does_not_start_a_process(managed, monkeypatch):
    env = managed
    before = (env.home / "config.yaml").read_bytes()
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr("nanocursor.mcp.settings.atomic_write", fail)
    result = await run(env, "start", name="docs", config={"command": "unused"})
    assert result.is_error and not env.clients
    assert (env.home / "config.yaml").read_bytes() == before


@pytest.mark.asyncio
async def test_concurrent_settings_edit_after_connection_is_preserved_and_new_client_closed(managed):
    env = managed
    def edit():
        if any(client.is_alive for client in env.clients):
            raw = read_saved(env)
            raw["custom_setting"]["edited"] = True
            (env.home / "config.yaml").write_text(yaml.safe_dump(raw))
    env.manager.on_change = edit
    result = await run(env, "start", name="docs", config={"command": "unused"})
    assert result.is_error and env.clients[-1].closed
    assert env.registry.get("mcp_docs_search") is None
    assert read_saved(env)["custom_setting"]["edited"] is True


def test_settings_edit_preserves_environment_only_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("DEEPSEEK_MODEL", "offline")
    settings = MCPSettings()
    settings.save({"name": "docs", "url": "https://docs.example/mcp"}, enabled=False, expected=b"")
    config = load_config(work_dir=tmp_path, env_provider="deepseek")
    assert config.selected_provider.model == "offline" and config.mcp_servers == []


@pytest.mark.asyncio
async def test_invalid_tool_schemas_or_names_never_publish_partial_service(managed, monkeypatch):
    env = managed
    def create(config):
        client = env.create(config)
        client.definitions.append(MCPTool(name="bad.name", inputSchema={"type": "object"}))
        return client
    monkeypatch.setattr("nanocursor.mcp.manager.MCPClient", create)
    result = await run(env, "start", name="docs", config={"command": "unused"})
    assert result.is_error and env.clients[-1].closed
    assert env.registry.get("mcp_docs_search") is None
    assert read_saved(env)["mcp_servers"][0]["enabled"] is False
