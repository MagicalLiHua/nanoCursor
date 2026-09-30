from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import TYPE_CHECKING

from nanocursor.commands.ports import CommandServices, MemoryControls, SessionActions

if TYPE_CHECKING:
    from nanocursor.commands.registry import CommandContext


def legacy_services(context: CommandContext) -> CommandServices:
    from nanocursor.status import collect_mcp_servers, collect_status

    config: Mapping[str, object] = getattr(context, "config", {})
    ui = context.ui
    tasks = getattr(ui, "task_manager", None)
    consolidator = getattr(ui, "_consolidator", None)
    status_source = ui if hasattr(ui, "agent") else SimpleNamespace(
        agent=getattr(context, "agent", None), conversation=getattr(context, "conversation", None),
        mcp_manager=getattr(ui, "mcp_manager", None),
        _mcp_server_configs=getattr(ui, "_mcp_server_configs", []),
    )
    return CommandServices(
        sessions=SessionActions(
            set_session=config.get("set_session"),
            set_conversation=config.get("set_conversation"),
            clear_chat=config.get("clear_chat"),
            render_restored=config.get("render_restored"),
            prepare_session_change=config.get("prepare_session_change"),
            has_active_tasks=tasks.has_active_tasks if tasks else lambda: False,
            is_running=lambda: bool(getattr(ui, "_streaming", False)),
            get_resume_candidates=lambda: getattr(ui, "_resume_candidates", ()),
            set_resume_candidates=lambda values: setattr(ui, "_resume_candidates", values),
        ),
        memory=MemoryControls(
            recall_changed=lambda value: setattr(ui, "_memory_recall_config", value)
            if hasattr(ui, "_memory_recall_config") else None,
            consolidation_status=consolidator.status_text if consolidator else None,
            set_consolidation=getattr(ui, "set_memory_consolidation", None),
        ),
        registry=config.get("registry"),
        skill_loader=config.get("skill_loader"),
        skill_executor=config.get("skill_executor"),
        register_owned_task=config.get("register_owned_task"),
        queue_skill_result=config.get("queue_skill_result"),
        session_id=config.get("session_id", ""),
        is_session_current=config.get("is_session_current", lambda session_id: True),
        status_snapshot=lambda: collect_status(status_source),
        mcp_servers=lambda: collect_mcp_servers(status_source),
        mcp_connecting=lambda: bool(getattr(ui, "_mcp_connecting", False)),
    )
