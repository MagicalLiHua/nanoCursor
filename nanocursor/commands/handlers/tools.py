from __future__ import annotations

from rich.markup import escape

from nanocursor.commands.registry import Command, CommandContext, CommandType
from nanocursor.mcp.tool_wrapper import MCPToolWrapper


async def handle_tools(ctx: CommandContext) -> None:
    selection = ctx.args.strip() or "all"
    if selection not in {"all", "enabled", "disabled"}:
        ctx.ui.add_system_message("用法：/tools [all|enabled|disabled]")
        return
    registry = getattr(ctx.agent, "registry", None)
    if registry is None:
        ctx.ui.add_system_message("工具系统尚未初始化")
        return
    tools = sorted(registry.list_tools(), key=lambda tool: tool.name.lower())
    enabled = sum(registry.is_enabled(tool.name) for tool in tools)
    deferred = set(registry.get_deferred_tool_names())
    lines = [f"工具列表 · {enabled} 已启用 / {len(tools)} 已注册",
             "已启用不代表免审批；待发现的工具可通过 ToolSearch 加载。"]
    shown = 0
    for tool in tools:
        active = registry.is_enabled(tool.name)
        if (selection == "enabled" and not active) or (selection == "disabled" and active):
            continue
        source = f"MCP: {tool._server_name}" if isinstance(tool, MCPToolWrapper) else "内置"
        state = "已启用 · 待发现" if active and tool.name in deferred else "已启用 · 已提供" if active else "已禁用"
        if isinstance(tool, MCPToolWrapper) and not tool._client.is_alive:
            state += " · 未连接"
        description = " ".join(tool.description.split())
        lines.append(f"\n{tool.name}  [{source}] {state}\n  {description[:160]}")
        shown += 1
    if not shown:
        lines.append("没有符合条件的工具。")
    ctx.ui.add_system_message(escape("\n".join(lines)))


TOOLS_COMMAND = Command(name="tools", description="查看工具列表与启用状态",
                        usage="/tools [all|enabled|disabled]", type=CommandType.LOCAL,
                        handler=handle_tools)
