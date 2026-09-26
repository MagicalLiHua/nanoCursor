from __future__ import annotations

import time
import copy

from nanocursor.commands.registry import Command, CommandType


async def _handle_rewind(ctx) -> None:
    tasks = getattr(ctx.ui, "task_manager", None)
    if getattr(ctx.ui, "_streaming", False) or (tasks and any(not t.done() for t in tasks._async_tasks.values())):
        ctx.ui.add_system_message("Wait for running tasks to finish before rewinding.")
        return
    fh = getattr(ctx.agent, "file_history", None)
    if fh is None or not fh.has_snapshots():
        ctx.ui.add_system_message("No checkpoints to rewind to.")
        return

    snapshots = fh.get_snapshots()

    lines = ["⟲ Rewind — select a checkpoint:\n"]
    for i, snap in enumerate(snapshots):
        ago = int(time.time() - snap.timestamp)
        label = snap.user_text[:50] + "…" if len(snap.user_text) > 50 else snap.user_text
        lines.append(f"  [{i + 1}] {label} ({ago}s ago, {len(snap.backups)} file(s))")
    lines.append("\nOptions after selecting:")
    lines.append("  1) Restore code and conversation")
    lines.append("  2) Restore conversation only")
    lines.append("  3) Restore code only")
    lines.append(f"\nUsage: /rewind <checkpoint> [option]  (e.g. /rewind {len(snapshots)} 1)")
    ctx.ui.add_system_message("\n".join(lines))

    args = ctx.args.strip()
    if not args:
        return

    parts = args.split()
    try:
        idx = int(parts[0]) - 1
    except (ValueError, IndexError):
        ctx.ui.add_system_message("Invalid checkpoint number.")
        return

    if idx < 0 or idx >= len(snapshots):
        ctx.ui.add_system_message(f"Checkpoint {idx + 1} not found. Valid: 1-{len(snapshots)}")
        return

    option = 1
    if len(parts) > 1:
        try:
            option = int(parts[1])
        except ValueError:
            ctx.ui.add_system_message("Invalid option. Use 1 (both), 2 (conversation), or 3 (code).")
            return

    snap = snapshots[idx]

    if option in (1, 2) and snap.conversation is None:
        ctx.ui.add_system_message("Conversation snapshot unavailable; use option 3 for code only.")
        return

    def restore_conversation():
        messages = copy.deepcopy(snap.conversation)
        if ctx.session:
            ctx.session.reset_history(messages)
        ctx.conversation.replace_history(messages)
        ctx.conversation.env_injected = snap.env_injected
        ctx.conversation.ltm_injected = snap.ltm_injected
        if ctx.agent:
            from nanocursor.context import create_replacement_state, RecoveryState
            ctx.agent.replacement_state = create_replacement_state()
            ctx.agent.recovery_state = RecoveryState()
            ctx.agent.clear_active_skills()
            controller = ctx.agent.approval_controller
            if controller:
                # Old approvals must not silently authorize a different branch
                # of the conversation. A new session restores complete context.
                from nanocursor.permissions.approval_context import AuthorizationContext
                controller.authorization = AuthorizationContext()
                controller.authorization.complete = False
                controller.revision += 1
                controller.persist_authorization()

    if option == 1:
        changed = fh.rewind(idx)
        restore_conversation()
        notice = f"⟲ Rewound to checkpoint {idx + 1}. Restored {len(changed)} file(s) and conversation."
    elif option == 2:
        restore_conversation()
        notice = f"⟲ Rewound conversation to checkpoint {idx + 1}. Files unchanged."
    elif option == 3:
        changed = fh.rewind(idx)
        notice = f"⟲ Restored {len(changed)} file(s) to checkpoint {idx + 1}. Conversation unchanged."
    else:
        ctx.ui.add_system_message("Invalid option. Use 1 (both), 2 (conversation), or 3 (code).")
        return

    if ctx.agent:
        ctx.agent._file_versions.clear()
    if option in (1, 2) and ctx.config.get("render_restored"):
        ctx.config["clear_chat"]()
        await ctx.config["render_restored"](ctx.conversation.history)
    ctx.ui.add_system_message(notice)
    if option in (1, 2) and ctx.agent and ctx.agent.approval_controller:
        ctx.ui.add_system_message("Conversation rewound. Model approval falls back to manual review until a new session.")


REWIND_COMMAND = Command(
    name="rewind",
    description="Rewind to a previous checkpoint",
    type=CommandType.LOCAL,
    handler=_handle_rewind,
    usage="/rewind [checkpoint_number] [option]",
)
