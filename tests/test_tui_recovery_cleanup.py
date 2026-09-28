"""Recovery gates stop optional maintenance, while leaving safe exit usable."""
from unittest.mock import AsyncMock

import pytest

from nanocursor.recovery import RecoveryRequired, RecoveryRuntime
from test_memory_ui import memory_app, command


@pytest.mark.asyncio
@pytest.mark.parametrize('blocked', ['storage_failed', 'restore_pending'])
async def test_tui_can_close_when_maintenance_must_not_start(memory_app, monkeypatch, blocked):
    make, _, _ = memory_app
    app = make()
    async with app.run_test(size=(90, 30)):
        runtime = app.recovery_runtime
        extract = AsyncMock()
        monkeypatch.setattr(app.agent, '_extract_memories', extract)
        if blocked == 'storage_failed':
            runtime.store.failed = True
        else:
            def require_restore(*args, **kwargs):
                raise RecoveryRequired(message='Restore needs user confirmation')
            monkeypatch.setattr(runtime, 'ensure_ready', require_restore)
        assert await app._shutdown_runtime()
        extract.assert_not_awaited()
        # Exit released the OS owner lock even though new work was forbidden.
        reopened = RecoveryRuntime.acquire(runtime.workspace.root)
        reopened.close()


@pytest.mark.asyncio
async def test_tui_recovery_gate_needs_direct_user_resolution(memory_app):
    make, client, _ = memory_app
    app = make()
    async with app.run_test(size=(90, 30)):
        runtime = app.recovery_runtime
        original = app.session.session_id
        operation = runtime.begin_operation('tool', 'database.update', {'key': 'example'})
        runtime.mark_unknown(operation, 'Response was lost')
        for request in ('/session new', '/clear', 'continue the task'):
            await command(app, request)
            assert app.session.session_id == original
            assert len(runtime.pending()) == 1
            assert client.main_history == []
        await command(app, '/recover')
        assert len(runtime.pending()) == 1
        await command(app, f'/recover acknowledge {operation} 已核查并接受仍未知的影响')
        assert runtime.pending() == []
        assert runtime.store.rows('SELECT state FROM operations WHERE operation_id=?', (operation,))[0]['state'] == 'outcome_unknown'
        assert client.main_history == []  # Resolving is not replay or a new model turn.
        await command(app, 'start a new task')
        if app._agent_task:
            await app._agent_task
        assert len(client.main_history) == 1
