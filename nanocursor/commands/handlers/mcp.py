from __future__ import annotations

from types import SimpleNamespace

from rich.markup import escape

from nanocursor.commands.registry import Command, CommandContext, CommandType
from nanocursor.status import collect_mcp_servers


async def handle_mcp(ctx: CommandContext) -> None:
    app = ctx.ui if hasattr(ctx.ui, "agent") else SimpleNamespace(
        agent=ctx.agent,
        mcp_manager=getattr(ctx.ui, "mcp_manager", None),
        _mcp_server_configs=getattr(ctx.ui, "_mcp_server_configs", []),
    )
    servers = collect_mcp_servers(app)
    connected = sum(server.connected for server in servers)
    lines = ["MCP 状态", f"{connected} 已连接 / {len(servers)} 已配置"]
    if getattr(ctx.ui, "_mcp_connecting", False):
        lines.append("正在连接；尚未连接的服务器可能仍在初始化。")
    if not servers:
        lines.append("尚未配置 MCP 服务器。")
    for server in servers:
        state = "已关闭" if server.stopped else "已连接" if server.connected else "未连接"
        lines.append(f"\n  {server.name}: {state} · 工具 {server.enabled} 已启用 / {len(server.tools)} 已注册")
        lines.extend(f"    - {name}" for name in server.tools[:10])
        if len(server.tools) > 10:
            lines.append(f"    … 另有 {len(server.tools) - 10} 个")
    lines.append("\n可让主 Agent 使用 ManageMCP 配置并启动或关闭服务；/tools 查看工具列表。")
    ctx.ui.add_system_message(escape("\n".join(lines)))


MCP_COMMAND = Command(
    name="mcp",
    aliases=[],
    description="显示 MCP 服务器状态",
    usage="/mcp",
    type=CommandType.LOCAL,
    handler=handle_mcp,
)
