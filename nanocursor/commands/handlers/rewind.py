from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone

from nanocursor.commands.registry import Command, CommandType
from nanocursor.conversation import Message
from nanocursor.filehistory.history import RewindError
from nanocursor.recovery import RecoveryError


def _show_preview(ctx, snapshot, preview, option: int) -> None:
    lines = [f"⟲ Checkpoint {snapshot.checkpoint_id}", f"Workspace: {snapshot.workspace_id}",
             "Preview only. Pause external editors before applying a restore."]
    if option in (1, 3):
        for item in preview.files:
            lines.append(f"  {item.action}: {item.path}")
            if item.diff:
                diff_lines = item.diff.splitlines()
                lines.extend(diff_lines[:60])
                if len(diff_lines) > 60:
                    lines.append(f"  … {len(diff_lines) - 60} additional diff lines")
        lines.extend(f"CONFLICT: {conflict}" for conflict in preview.conflicts)
        if not preview.files and not preview.conflicts:
            lines.append("No recorded file changes to restore.")
        lines.extend(f"Coverage: {entry}" for entry in preview.uncovered)
    if option == 2:
        lines.append("Conversation only: file changes and external effects will remain.")
    if option == 2 or not preview.conflicts:
        lines.append(f"Confirm: /rewind {snapshot.checkpoint_id} {option} apply")
    ctx.ui.add_system_message("\n".join(lines))


def _restore_conversation(ctx, snapshot, restore_info) -> None:
    option = restore_info["option"]
    messages = copy.deepcopy(snapshot.conversation)
    if option == 2:
        messages.append(Message("user", "<system-reminder>Only conversation history was rewound. File changes and external effects were not undone.</system-reminder>"))
    if ctx.session:
        from nanocursor.memory.session import SessionMetadataError
        try:
            ctx.session.reset_history(messages, record_id=restore_info["conversation_record_id"])
        except SessionMetadataError as exc:
            ctx.ui.add_system_message(f"回退记录已保存，但会话元数据更新失败: {exc}")
    ctx.conversation.replace_history(messages)
    ctx.conversation.env_injected = snapshot.env_injected
    ctx.conversation.ltm_injected = snapshot.ltm_injected
    if ctx.agent:
        from nanocursor.context import create_replacement_state, RecoveryState
        ctx.agent.replacement_state = create_replacement_state()
        ctx.agent.recovery_state = RecoveryState()
        ctx.agent.clear_active_skills()
        controller = ctx.agent.approval_controller
        if controller:
            from nanocursor.permissions.approval_context import AuthorizationContext
            controller.authorization = AuthorizationContext()
            controller.authorization.complete = False
            controller.revision += 1
            controller.persist_authorization()


def _append_code_notice(ctx, restore_info) -> None:
    content = (f"<system-reminder>File restore {restore_info['restore_id']} restored the recorded file-tool edits "
               f"to checkpoint {restore_info['checkpoint_id']}. Conversation history was retained. "
               "Bash, MCP, Git and project-external effects are not covered and were not reverted.</system-reminder>")
    message = Message("user", content)
    if ctx.session:
        from nanocursor.memory.session import SessionRecord, RecordType, SessionMetadataError
        record = SessionRecord(RecordType.USER, content,
                               datetime.fromtimestamp(restore_info["created"], timezone.utc),
                               record_id=restore_info["conversation_record_id"])
        try:
            ctx.session.append_record(record)
        except SessionMetadataError as exc:
            ctx.ui.add_system_message(f"恢复提示已保存，但会话元数据更新失败: {exc}")
    if not any(m.content == content for m in ctx.conversation.history):
        ctx.conversation.history.append(message)


async def _handle_rewind(ctx) -> None:
    tasks = getattr(ctx.ui, "task_manager", None)
    if getattr(ctx.ui, "_streaming", False) or (tasks and tasks.has_active_tasks()):
        ctx.ui.add_system_message("Wait for running tasks to finish before rewinding.")
        return
    fh = getattr(ctx.agent, "file_history", None)
    if fh is None or not fh.has_snapshots():
        ctx.ui.add_system_message("No checkpoints to rewind to.")
        return
    args = ctx.args.strip().split()
    snapshots = fh.get_snapshots()
    if not args:
        lines = ["⟲ Persistent checkpoints — preview before restoring:"]
        for i, snapshot in enumerate(snapshots):
            age = max(0, int(time.time() - snapshot.timestamp))
            lines.append(f"  [{i + 1}] {snapshot.checkpoint_id} {snapshot.user_text[:60]} ({age}s ago)")
        lines.append("Options: 1 code + conversation; 2 conversation only; 3 code only.")
        lines.append("Usage: /rewind <number-or-id> [option]; then /rewind <stable-id> <option> apply")
        for pending in fh.pending_restores():
            lines.append(f"Unfinished restore {pending['restore_id']} ({pending['state']}): /rewind resume {pending['restore_id']}")
        ctx.ui.add_system_message("\n".join(lines))
        return

    if args[0] in ("inspect", "storage", "cleanup", "pin", "unpin"):
        try:
            if args[0] == "inspect" and len(args) == 2:
                ctx.ui.add_system_message(json.dumps(fh.inspect_checkpoint(args[1]), ensure_ascii=False, indent=2)
                                          + "\nRead-only evidence. Preserve user changes before manually copying a backup; this does not authorize automatic restore.")
            elif args[0] == "storage" and len(args) == 1:
                info = fh.retention_info()
                ctx.ui.add_system_message(f"Checkpoints: {info['checkpoints']} (soft limit {info['soft_limit']}); "
                                          f"referenced recovery content across workspaces: {info['referenced_bytes_all_workspaces']} bytes.\n"
                                          + info["reason"])
            elif args[0] == "cleanup" and len(args) == 1:
                runtime = getattr(ctx.agent, "recovery", None)
                if runtime:
                    runtime.ensure_workspace_idle()
                count = fh.prune_unreferenced(keep=0)
                ctx.ui.add_system_message(f"Removed {count} unreferenced checkpoints. File versions and restore evidence were retained.")
            elif args[0] in ("pin", "unpin") and len(args) == 2:
                fh.pin_checkpoint(args[1], pinned=args[0] == "pin")
                ctx.ui.add_system_message(f"Checkpoint {args[1]} {'pinned' if args[0] == 'pin' else 'unpinned'}.")
            else:
                raise RewindError("Usage: /rewind inspect <id> | storage | cleanup | pin <id> | unpin <id>")
        except (RewindError, RecoveryError, OSError) as exc:
            ctx.ui.add_system_message(f"Checkpoint maintenance stopped: {exc}")
        return

    try:
        resuming = args[0] == "resume"
        if resuming:
            if len(args) not in (2, 3) or (len(args) == 3 and args[2] != "apply"):
                raise RewindError("Usage: /rewind resume <restore-id> [apply]")
            restore_id = args[1]
            info = fh.restore_info(restore_id)
            snapshot = fh.snapshot(info["checkpoint_id"])
            option = info["option"]
            if len(args) == 2:
                lines = [f"Restore {restore_id}: {info['state']}"]
                for item in info["items"]:
                    before = json.loads(item["before_state"])
                    lines.append(f"  {item['path']}: {item['observed']} (recorded {item['state']})")
                    lines.append("    Before-restore copy: " + (before["backup_path"] if before["exists"] else "file did not exist"))
                lines.append(f"Confirm remaining steps: /rewind resume {restore_id} apply")
                ctx.ui.add_system_message("\n".join(lines))
                return
            if info["state"] == "complete":
                ctx.ui.add_system_message(f"Restore {restore_id} already completed; nothing was replayed.")
                return
        else:
            if len(args) > 3 or (len(args) == 3 and args[2] != "apply"):
                raise RewindError("Usage: /rewind <checkpoint> [1|2|3] [apply]")
            identifier = int(args[0]) - 1 if args[0].isdigit() else args[0]
            snapshot = fh.snapshot(identifier)
            try:
                option = int(args[1]) if len(args) > 1 else 1
            except ValueError as exc:
                raise RewindError("Invalid option. Use 1 (both), 2 (conversation), or 3 (code).") from exc
            if option not in (1, 2, 3):
                raise RewindError("Invalid option. Use 1 (both), 2 (conversation), or 3 (code).")
            if option in (1, 2) and snapshot.conversation is None:
                raise RewindError("Conversation snapshot unavailable; use option 3 for code only.")
            preview = fh.preview(snapshot.checkpoint_id)
            if len(args) < 3:
                _show_preview(ctx, snapshot, preview, option)
                return
            if args[0] != snapshot.checkpoint_id:
                raise RewindError("Apply requires the full stable checkpoint ID shown in the preview.")
            restore_id = None

        runtime = getattr(ctx.agent, "recovery", None)
        if runtime is not None:
            runtime.ensure_workspace_idle(allow_restore_id=restore_id)
        prepare = getattr(ctx, "config", {}).get("prepare_session_change")
        if prepare:
            await prepare()
        if not resuming:
            restore_id = fh.start_restore(snapshot.checkpoint_id, option=option)
        changed = fh.apply_restore(restore_id)
        info = fh.restore_info(restore_id)
        if option in (1, 2):
            _restore_conversation(ctx, snapshot, info)
        else:
            _append_code_notice(ctx, info)
        fh.complete_restore(restore_id)
        if ctx.agent:
            ctx.agent._file_versions.clear()
        if option in (1, 2) and ctx.config.get("render_restored"):
            ctx.config["clear_chat"]()
            await ctx.config["render_restored"](ctx.conversation.history)
        ctx.ui.add_system_message(f"⟲ Restore {restore_id} complete: {len(changed)} recorded file(s); "
                                  + ("conversation restored." if option in (1, 2) else "conversation retained with restore notice."))
        if option in (1, 2) and ctx.agent and ctx.agent.approval_controller:
            ctx.ui.add_system_message("Conversation rewound. Model approval falls back to manual review until a new session.")
    except (RewindError, RecoveryError, OSError) as exc:
        ctx.ui.add_system_message(f"Rewind stopped: {exc}")


REWIND_COMMAND = Command(
    name="rewind", description="Preview and explicitly restore persistent file checkpoints",
    type=CommandType.LOCAL, handler=_handle_rewind,
    usage="/rewind [checkpoint] [option] [apply] | /rewind resume <restore-id> [apply]",
)
