"""Recovery is a direct user command; it is deliberately not a model tool."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex

from nanocursor.commands.registry import Command, CommandType
from nanocursor.runtime import redact


def recovery_report(runtime) -> dict:
    pending = runtime.pending()
    for operation in pending:
        operation["process"] = runtime.store.get_metadata("process", operation["operation_id"])
        operation["file_observations"] = file_observations(runtime.store, operation["operation_id"])
    restores = runtime.store.rows("SELECT r.restore_id,r.workspace_id,r.session_id,r.checkpoint_id,r.state FROM file_restores r JOIN workspaces w USING(workspace_id) WHERE w.project_id=? AND r.state != 'complete'", (runtime.project_id,)) if runtime.store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='file_restores'") else []
    return {
        'workspace': runtime.workspace.root,
        'blocked': bool(pending or restores),
        'unknown_operations': pending,
        'pending_restores': restores,
        'live_operations': runtime.live_operations(),
        'notice': 'No operation was replayed. Acknowledging uncertainty preserves the original record; it does not prove success or undo effects.',
    }


def file_observations(store, operation_id) -> list[dict]:
    """Compare only recorded local file effects; never query external services."""
    if not store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='file_edit_records'"):
        return []
    from nanocursor.tools.file_io import validate_regular_path, MAX_FILE_BYTES
    observations = []
    for row in store.rows("SELECT path,before_state,after_state FROM file_edit_records WHERE operation_id=?", (operation_id,)):
        before, after = json.loads(row['before_state']), json.loads(row['after_state'])
        observation = {'path': row['path'], 'before_hash': before.get('digest'), 'after_hash': after.get('digest')}
        try:
            path = validate_regular_path(row['path'])
            if path.exists():
                if path.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError('Current file exceeds bounded recovery inspection size')
                with path.open('rb') as stream:
                    content = stream.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError('Current file exceeds bounded recovery inspection size')
                digest = hashlib.sha256(content).hexdigest()
                observation['current_hash'] = digest
                observation['matches'] = 'recorded after' if after['exists'] and digest == after['digest'] else 'recorded before' if before['exists'] and digest == before['digest'] else 'neither'
            else:
                observation['matches'] = 'recorded after (absent)' if not after['exists'] else 'recorded before (absent)' if not before['exists'] else 'neither (absent)'
        except (OSError, ValueError) as exc:
            observation['inspection_error'] = str(exc)
        observation['notice'] = 'Content comparison only; it does not establish who wrote the file or whether other effects completed.'
        observations.append(observation)
    return observations


def busy_report(store, work_dir) -> dict:
    """Inspect an active checkout without changing its owner or execution facts."""
    identity = store.register_workspace(work_dir)
    rows = store.rows("SELECT * FROM operations WHERE project_id=? AND state IN ('intent','outcome_unknown') ORDER BY created_at", (identity.project_id,))
    for row in rows:
        row['arguments'] = json.loads(row['arguments'])
    unresolved = {row['operation_id'] for row in store.rows(
        "SELECT o.operation_id FROM operations o WHERE project_id=? AND state='outcome_unknown' AND NOT EXISTS(SELECT 1 FROM resolutions r WHERE r.operation_id=o.operation_id)", (identity.project_id,))}
    return {'workspace': identity.root, 'read_only': True, 'owner_active': True,
            'blocked': bool(unresolved), 'unknown_operations': [r for r in rows if r['operation_id'] in unresolved],
            'live_operations': [r for r in rows if r['state'] == 'intent'], 'pending_restores': [],
            'notice': 'Read-only inspection. Another process owns this checkout; unfinished operations are not classified as crashed.'}


def format_report(report: dict) -> str:
    rows = report['unknown_operations']
    lines = ['恢复检查：' + ('需要人工确认以下未完成操作。' if rows else '没有待确认的未知执行结果。')]
    if report.get('owner_active'):
        lines.append('只读查看：另一个进程仍持有工作区。未完成操作不等于已经崩溃。')
    for row in rows:
        arguments = json.dumps(row['arguments'], ensure_ascii=False)
        if len(arguments) > 600:
            arguments = arguments[:600] + '…（完整参数见 recover --json）'
        lines.extend([
            f"  {row['operation_id']}  {row['kind']} / {row['name']}",
            f"    时间: {row['created_at']}  目录: {row['cwd']}",
            f"    参数: {arguments}",
            f"    状态: {row['observation'] or '结果未知'}",
        ])
        if row.get('process'):
            lines.append(f"    进程线索: PID {row['process']['pid']}，最后观察退出码 {row['process']['exit_code']}；不代表当前进程身份。")
        for observed in row.get('file_observations', []):
            lines.append(f"    文件核对: {observed['path']} → {observed.get('matches', observed.get('inspection_error'))}（仅内容证据）")
    for restore in report.get('pending_restores', []):
        lines.append(f"文件恢复待完成: {restore['restore_id']}，会话 {restore['session_id']}；先恢复该会话，再 /rewind resume {restore['restore_id']} 查看。")
    if report['live_operations']:
        lines.append(f"另有 {len(report['live_operations'])} 项操作仍由执行进程持有，不能按已中断处理。")
        for live in report['live_operations']:
            lines.append(f"  {live['kind']} / {live['name']}  {live['operation_id']}")
        if any(live['kind'] == 'mcp_server' for live in report['live_operations']):
            lines.append('MCP 服务仍在运行；文件回退或删除工作树前请先停止相关服务。')
    lines.append('不会自动重放。确认前请自行检查实际影响；历史记录不代表资源当前状态。')
    if rows:
        lines.append('确认接受不确定性：/recover acknowledge <operation_id> <检查说明>')
    return redact('\n'.join(lines))


async def handle_recover(ctx) -> None:
    runtime = getattr(ctx.agent, 'recovery', None)
    if runtime is None:
        ctx.ui.add_system_message('恢复服务未初始化。')
        return
    parts = shlex.split(ctx.args)
    if parts and parts[0] == 'acknowledge':
        if len(parts) < 3:
            ctx.ui.add_system_message('用法：/recover acknowledge <operation_id> <检查说明>')
            return
        runtime.acknowledge(parts[1], ' '.join(parts[2:]))
        ctx.ui.add_system_message('已记录你的确认。原始未知状态保留；后续操作仍须正常审批。')
    elif parts and parts[0] not in {'list', 'status'}:
        ctx.ui.add_system_message('用法：/recover [list | acknowledge <operation_id> <检查说明>]')
        return
    ctx.ui.add_system_message(format_report(recovery_report(runtime)))


RECOVER_COMMAND = Command(name='recover', description='检查中断操作并记录人工恢复决定',
                          usage='/recover [list | acknowledge <id> <说明>]',
                          type=CommandType.LOCAL, handler=handle_recover)
