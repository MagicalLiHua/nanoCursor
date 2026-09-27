"""Status accounting uses live local state, without model or server requests."""

from dataclasses import FrozenInstanceError
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from mcp.types import Tool as MCPTool
from rich.text import Text

from nanocursor.client import AnthropicClient, OpenAIClient, OpenAICompatClient
from nanocursor.commands.handlers.mcp import handle_mcp
from nanocursor.commands.handlers.status import handle_status
from nanocursor.commands.registry import CommandContext
from nanocursor.config import MCPServerConfig, ProviderConfig
from nanocursor.conversation import ConversationManager, Message
from nanocursor.mcp.client import MCPClient
from nanocursor.mcp.manager import MCPManager
from nanocursor.mcp.tool_wrapper import MCPToolWrapper
from nanocursor.permissions import PermissionMode
from nanocursor.status import StatusSnapshot, collect_mcp_servers, collect_status, format_status_details
from nanocursor.tools import create_default_registry


def make_app(protocol="openai-compat", model="deepseek-chat", thinking=False):
    provider = ProviderConfig("test", protocol, "https://invalid.example", model,
                              thinking=thinking, context_window=128_000)
    client_class = {"anthropic": AnthropicClient, "openai": OpenAIClient,
                    "openai-compat": OpenAICompatClient}[protocol]
    # No SDK, credential loading, or API requests are needed for status.
    client = object.__new__(client_class)
    client.model = provider.model
    if protocol == "anthropic":
        client.thinking = thinking
    registry = create_default_registry()
    agent = NS(client=client, registry=registry, permission_mode=PermissionMode.DEFAULT,
               approval_controller=None, context_window=provider.get_context_window(),
               total_input_tokens=90_000, total_output_tokens=5_000, work_dir="/project")
    messages = []
    return NS(agent=agent, client=client, registry=registry, conversation=ConversationManager(),
              _selected_provider=provider, _mcp_server_configs=[], mcp_manager=None,
              _mcp_connecting=False, messages=messages, add_system_message=messages.append)


def command_context(app):
    return CommandContext("", app.agent, app.conversation, None, None, None, app, {})


def test_context_uses_live_anchor_and_resets_after_compaction():
    app = make_app()
    app.conversation.add_user_message("task")
    app.conversation.add_assistant_message("reply")
    app.conversation.record_usage_anchor(1_000, 100, cache_read=8_000, cache_creation=200)
    anchored = collect_status(app)
    assert anchored.context_used == 9_300
    assert anchored.context_source == "最近 API 用量锚点"
    assert anchored.input_tokens == 90_000
    app.conversation.add_user_message("a" * 350)
    assert collect_status(app).context_used == 9_400
    assert "新增消息" in collect_status(app).context_source
    app.conversation.replace_history([Message("user", "a" * 700)])
    compacted = collect_status(app)
    assert compacted.context_used == 200
    assert compacted.context_source == "字符估算"
    assert compacted.input_tokens == anchored.input_tokens
    assert compacted.output_tokens == anchored.output_tokens


@pytest.mark.parametrize("protocol,thinking,expected", [
    ("anthropic", True, "思考开启"),
    ("anthropic", False, "默认"),
    ("openai", True, "默认"),
    ("openai-compat", True, "默认"),
])
def test_reasoning_reflects_actual_client_request(protocol, thinking, expected):
    app = make_app(protocol=protocol, thinking=thinking)
    assert collect_status(app).reasoning == expected


def test_reasoning_follows_client_if_config_is_changed_without_recreating_it():
    app = make_app(protocol="anthropic", thinking=True)
    app._selected_provider.thinking = False
    assert collect_status(app).reasoning == "思考开启"


@pytest.mark.parametrize("model,configured,fetched,expected", [
    ("deepseek-chat", 64000, 0, "显式配置"),
    ("deepseek-chat", 0, 64000, "服务商元数据"),
    ("gpt-4o", 0, 0, "内置模型映射"),
    ("unknown-provider-model", 0, 0, "默认回退值（可配置覆盖）"),
])
def test_context_window_source(model, configured, fetched, expected):
    app = make_app(model=model)
    app._selected_provider.context_window = configured
    app._selected_provider.set_fetched_context_window(fetched)
    app.agent.context_window = app._selected_provider.get_context_window()
    assert collect_status(app).window_source == expected


def install_mcp(app):
    configs = [MCPServerConfig(name="online", command="unused"),
               MCPServerConfig(name="offline", command="unused"),
               MCPServerConfig(name="failed", command="unused")]
    app._mcp_server_configs = configs
    app.mcp_manager = manager = MCPManager()
    manager.load_configs(configs)
    for cfg in configs[:2]:
        client = MCPClient(cfg)
        client._alive = cfg.name == "online"
        manager._clients[cfg.name] = client
        wrapper = MCPToolWrapper(cfg.name, MCPTool(
            name="search", inputSchema={"type": "object"}), client)
        app.registry.register(wrapper)


def test_deferred_and_disabled_tools_are_not_counted_as_visible():
    app = make_app()
    install_mcp(app)
    app.registry.disable("Bash")
    s = collect_status(app)
    assert (s.tools_enabled, s.tools_visible, s.tools_builtin, s.tools_mcp) == (7, 5, 5, 2)
    assert (s.mcp_connected, s.mcp_configured) == (1, 3)
    app.registry.mark_discovered("mcp_online_search")
    assert collect_status(app).tools_visible == 6
    app.registry.disable("mcp_online_search")
    s = collect_status(app)
    assert (s.tools_enabled, s.tools_visible, s.tools_mcp) == (6, 5, 1)
    app.registry.enable("mcp_online_search")
    assert collect_status(app).tools_visible == 6


def test_mcp_disconnection_and_configuration_deduplication():
    app = make_app()
    install_mcp(app)
    app._mcp_server_configs.append(app._mcp_server_configs[0])
    servers = collect_mcp_servers(app)
    assert [(s.name, s.connected, s.enabled) for s in servers] == [
        ("online", True, 1), ("offline", False, 1), ("failed", False, 0)]
    app.mcp_manager._clients["online"]._alive = False
    s = collect_status(app)
    assert (s.mcp_connected, s.mcp_configured) == (0, 3)
    assert s.tools_mcp == 2  # Registered and enabled is independent of connection.


@pytest.mark.asyncio
async def test_mcp_command_includes_failed_servers_and_real_wrapper_tool_counts():
    app = make_app()
    install_mcp(app)
    app.registry.disable("mcp_offline_search")
    await handle_mcp(command_context(app))
    text = Text.from_markup(app.messages[-1]).plain
    assert "1 已连接 / 3 已配置" in text
    assert "online: 已连接 · 工具 1 已启用 / 1 已注册" in text
    assert "offline: 未连接 · 工具 0 已启用 / 1 已注册" in text
    assert "failed: 未连接 · 工具 0 已启用 / 0 已注册" in text
    assert "- search" in text


@pytest.mark.asyncio
async def test_mcp_command_shows_configuration_even_when_all_connections_fail():
    app = make_app()
    app._mcp_server_configs = [MCPServerConfig(name="broken", command="unused")]
    await handle_mcp(command_context(app))
    assert "0 已连接 / 1 已配置" in app.messages[-1]
    assert "broken: 未连接" in app.messages[-1]


@pytest.mark.asyncio
async def test_status_shares_snapshot_and_preserves_markup_in_model_names():
    app = make_app(model="[red]custom[/red]")
    app.conversation.add_user_message("a" * 350)
    snapshot = collect_status(app)
    text = format_status_details(snapshot)
    assert "模型: [red]custom[/red]" in text
    assert "上下文: 约 100 / 128,000" in text
    await handle_status(command_context(app))
    displayed = Text.from_markup(app.messages[-1]).plain
    assert displayed.startswith(text)
    assert "模型: [red]custom[/red]" in displayed
    assert "输入 90,000" in displayed
    assert "输入不含缓存读取与缓存创建" in displayed


def test_collection_is_in_memory_and_has_immutable_defaults(monkeypatch):
    app = make_app()
    install_mcp(app)
    fail = Mock(side_effect=AssertionError("status must not probe external state"))
    monkeypatch.setattr(ProviderConfig, "resolve_api_key", fail)
    monkeypatch.setattr(MCPClient, "connect", fail)
    monkeypatch.setattr(MCPManager, "get_client", fail)
    bash = app.registry.get("Bash")
    bash.sandbox = NS(available=fail)
    bash.sandbox_config = NS()
    monkeypatch.setattr(bash, "execution_details", fail)
    assert collect_status(app).sandbox == "已配置"
    fail.assert_not_called()
    with pytest.raises(FrozenInstanceError):
        StatusSnapshot().context_used = 1
    assert collect_status(NS()) == StatusSnapshot()


@pytest.mark.asyncio
async def test_approval_activity_follows_review_and_manual_resolution():
    app = make_app()
    app.agent.approval_controller = NS(config=NS(mode="smart"),
                                       active=lambda mode: True,
                                       authorization=NS(complete=True))
    assert collect_status(app).approval == "smart"
    app._last_approval_status = "正在审批 · test"
    assert collect_status(app).approval == "审查中"
    app._last_approval_status = "需人工确认 · 请确认影响范围"
    app._pending_perm_request = NS(future=asyncio.get_running_loop().create_future())
    assert collect_status(app).approval == "待确认"
    app._pending_perm_request.future.set_result("deny")
    assert collect_status(app).approval == "smart"
    app.agent.approval_controller = None
    app._last_approval_status = "正在审批 · 旧记录"
    assert collect_status(app).approval == "人工"
