"""Read-only, in-memory status shared by the terminal and slash commands.

The strings returned here are plain text, not Rich markup. Rendering callers
must preserve them as text, including model and server names supplied by users.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nanocursor.client import AnthropicClient
from nanocursor.config import ProviderConfig
from nanocursor.conversation import ConversationManager
from nanocursor.mcp.tool_wrapper import MCPToolWrapper
from nanocursor.validator import lookup_model_context_window


@dataclass(frozen=True)
class StatusSnapshot:
    model: str = ""
    reasoning: str = "默认"
    context_used: int = 0
    context_window: int = 0
    context_source: str = "字符估算"
    window_source: str = "未初始化"
    input_tokens: int = 0
    output_tokens: int = 0
    usage_missing_requests: int = 0
    permission_mode: str = "default"
    approval: str = "人工"
    sandbox: str = "关闭"
    mcp_connected: int = 0
    mcp_configured: int = 0
    mcp_connecting: bool = False
    tools_enabled: int = 0
    tools_visible: int = 0
    tools_builtin: int = 0
    tools_mcp: int = 0
    teammates: int = 0
    approval_notice: str = ""
    memory_recall: str = ""


@dataclass(frozen=True)
class MCPServerStatus:
    name: str
    connected: bool
    tools: tuple[str, ...] = ()
    enabled: int = 0
    stopped: bool = False


def _count(value: Any) -> int:
    return max(value, 0) if isinstance(value, int) and not isinstance(value, bool) else 0


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _registered_tools(app: Any) -> tuple[Any, list[Any]]:
    agent = getattr(app, "agent", None)
    registry = getattr(agent, "registry", None) if agent is not None else None
    if registry is None:
        registry = getattr(app, "registry", None)
    return registry, list(registry.list_tools()) if registry is not None else []


def collect_mcp_servers(app: Any) -> tuple[MCPServerStatus, ...]:
    """Include configured failures as well as established client sessions.

    Read only the manager's local maps; never connect or probe a service here.
    """
    manager = getattr(app, "mcp_manager", None)
    clients = getattr(manager, "_clients", {}) if manager is not None else {}
    configs = getattr(manager, "_configs", {}) if manager is not None else {}
    names = dict.fromkeys(
        cfg.name for cfg in getattr(app, "_mcp_server_configs", [])
    )
    names.update(dict.fromkeys(configs))
    names.update(dict.fromkeys(clients))
    registry, tools = _registered_tools(app)
    result = []
    for name in names:
        server_tools = [tool for tool in tools
                        if isinstance(tool, MCPToolWrapper) and tool._server_name == name]
        result.append(MCPServerStatus(
            name=name,
            connected=bool(getattr(clients.get(name), "is_alive", False)),
            tools=tuple(tool.mcp_tool_name for tool in server_tools),
            enabled=sum(registry.is_enabled(tool.name) for tool in server_tools),
            stopped=name in getattr(manager, "_disabled", set()),
        ))
    return tuple(result)


def _window_source(provider: Any, window: int) -> str:
    if not window:
        return "未初始化"
    if not isinstance(provider, ProviderConfig) or provider.get_context_window() != window:
        return "当前 Agent 配置"
    if provider.context_window > 0:
        return "显式配置"
    if provider._fetched_context_window > 0:
        return "服务商元数据"
    if lookup_model_context_window(provider.model) > 0:
        return "内置模型映射"
    return "默认回退值（可配置覆盖）"


def collect_status(app: Any) -> StatusSnapshot:
    """Take a cheap snapshot without probes, file reads, or additional API calls."""
    agent = getattr(app, "agent", None)
    provider = getattr(app, "_selected_provider", None)
    client = getattr(agent, "client", None) if agent is not None else None
    if client is None:
        client = getattr(app, "client", None)
    model = _text(getattr(client, "model", "")) or _text(getattr(provider, "model", ""))
    # Other clients do not send config.thinking or an effort parameter. A model
    # may still reason by default, so "默认" must not be presented as "关闭".
    reasoning = "思考开启" if isinstance(client, AnthropicClient) and client.thinking else "默认"
    conv = getattr(app, "conversation", None)
    context_used = conv.current_tokens() if isinstance(conv, ConversationManager) else 0
    context_source = "字符估算"
    if isinstance(conv, ConversationManager) and conv.baseline_tokens > 0:
        context_source = "最近 API 用量锚点"
        if len(conv.history) > conv.anchor_count:
            context_source += " + 新增消息字符估算"
    window = _count(getattr(agent, "context_window", 0))
    if not window and isinstance(provider, ProviderConfig):
        window = provider.get_context_window()
    raw_mode = getattr(agent, "permission_mode", None)
    permission_mode = _text(getattr(raw_mode, "value", raw_mode), "default")
    controller = getattr(agent, "approval_controller", None)
    approval_notice = _text(getattr(app, "_last_approval_status", ""))
    approval = "人工"
    if controller is not None and getattr(controller.config, "mode", "") == "smart":
        approval = "smart" if controller.active(raw_mode) else "smart 暂停"
        authorization = getattr(controller, "authorization", None)
        if approval == "smart" and getattr(authorization, "complete", True) is False:
            approval = "smart 转人工"
        if approval == "smart" and approval_notice.startswith("正在审批"):
            approval = "审查中"
    pending_request = getattr(app, "_pending_perm_request", None)
    if pending_request is not None and not pending_request.future.done():
        approval = "待确认"

    registry, tools = _registered_tools(app)
    enabled = [tool for tool in tools if registry.is_enabled(tool.name)]
    deferred = set(registry.get_deferred_tool_names()) if registry is not None else set()
    mcp_tools = sum(isinstance(tool, MCPToolWrapper) for tool in enabled)
    bash = registry.get("Bash") if registry is not None else None
    # Enabling the sandbox already validates the backend. Do not probe PATH or
    # the filesystem on each render; execute() checks availability again.
    sandbox_configured = (getattr(bash, "sandbox", None) is not None
                          and getattr(bash, "sandbox_config", None) is not None)
    servers = collect_mcp_servers(app)
    team_manager = getattr(app, "team_manager", None)
    progress = team_manager.get_all_teammate_progress() if team_manager is not None else []
    return StatusSnapshot(
        model=model,
        reasoning=reasoning,
        context_used=context_used,
        context_window=window,
        context_source=context_source,
        window_source=_window_source(provider, window),
        input_tokens=_count(getattr(agent, "total_input_tokens", 0)),
        output_tokens=_count(getattr(agent, "total_output_tokens", 0)),
        usage_missing_requests=_count(getattr(agent, "usage_missing_requests", 0)),
        permission_mode=permission_mode,
        approval=approval,
        sandbox="已配置" if sandbox_configured else "关闭",
        mcp_connected=sum(server.connected for server in servers),
        mcp_configured=len(servers),
        mcp_connecting=bool(getattr(app, "_mcp_connecting", False)),
        tools_enabled=len(enabled),
        tools_visible=sum(tool.name not in deferred for tool in enabled),
        tools_builtin=len(enabled) - mcp_tools,
        tools_mcp=mcp_tools,
        teammates=sum(item.status == "running" for item in progress),
        memory_recall=(_text(agent.memory_recall.status_text())
                       if getattr(agent, "memory_manager", None) and getattr(agent, "memory_recall", None) else ""),
        approval_notice=approval_notice,
    )


def format_status_details(snapshot: StatusSnapshot, *, heading: bool = True) -> str:
    """Explain status with the same accounting as the persistent status bar."""
    s = snapshot
    context = f"约 {s.context_used:,} / {s.context_window:,}"
    if s.context_window:
        context += f"（{s.context_used / s.context_window:.1%}）"
    else:
        context = f"约 {s.context_used:,} / 未初始化"
    usage = f"Token 累计: 输入 {s.input_tokens:,} · 输出 {s.output_tokens:,}"
    if s.usage_missing_requests:
        usage = (usage + "（仅已上报部分）" if s.input_tokens or s.output_tokens
                 else "Token 累计: —（服务未返回用量）")
    lines = ["NanoCursor 状态"] if heading else []
    lines += [
        f"模型: {s.model or '未选择'}",
        f"推理: {s.reasoning}（未指定努力档位）",
        f"模式: {s.permission_mode} · 审批: {s.approval}",
        f"Bash 沙箱: {s.sandbox}",
        f"上下文: {context}",
        usage,
        f"MCP: {s.mcp_connected} 已连接 / {s.mcp_configured} 已配置"
        + ("（正在连接）" if s.mcp_connecting else ""),
        f"工具: {s.tools_visible} 当前可见 / {s.tools_enabled} 已启用"
        f"（内置 {s.tools_builtin} · MCP {s.tools_mcp}）",
        f"队友: {s.teammates} 正在运行",
    ]
    if s.approval_notice:
        lines.append(f"最近审批: {s.approval_notice}")
    if s.memory_recall:
        lines += ["", s.memory_recall]
    if s.usage_missing_requests:
        lines.append(f"用量缺失: {s.usage_missing_requests} 次请求未返回用量，累计仅含已上报部分。")
    lines += [
        "",
        f"占用来源: {s.context_source}",
        f"窗口来源: {s.window_source}",
        "累计仅主 Agent 当前运行；/clear 清零，不代表上下文或费用。",
        "输入不含缓存读取与缓存创建；累计不含其他模型调用。",
    ]
    return "\n".join(lines)
