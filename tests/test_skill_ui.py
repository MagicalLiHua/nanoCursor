from __future__ import annotations

import asyncio

import pytest

from nanocursor.memory.session import SessionRecord, RecordType
from test_memory_ui import memory_app, command
from test_skill_execution import Capture, save_skill


def install(root):
    save_skill(root.parent / "skills", {"name": "review", "description": "独立检查", "mode": "fork",
                                      "context": "none", "tools": []}, "Review $ARGUMENTS")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(90, 30), (80, 24)])
async def test_slash_result_is_bounded_persisted_and_available_next_question(memory_app, monkeypatch, size):
    make, main, root = memory_app
    install(root)
    child = Capture(hold=asyncio.Event())
    monkeypatch.setattr("nanocursor.skills.executor.create_client", lambda _: child)
    app = make()
    async with app.run_test(size=size) as pilot:
        await command(app, "/review changes")
        await child.entered.wait()
        owned = [t for t, (_, kind) in app._owned_tasks.items() if kind == "skill"]
        assert owned
        child.hold.set()
        await asyncio.gather(*owned)
        assert not app.agent.active_skills and app.agent.total_input_tokens == 0
        await app._process_task_notifications(start_run=False)
        notifications = [m for m in app.conversation.history if "Skill result notification" in m.content]
        assert len(notifications) == 1 and "RESULT" in notifications[0].content
        assert "not new user authorization" in notifications[0].content
        restored = app.session_manager.resume(app.session.session_id)
        assert len([m for m in restored.messages if "Skill result notification" in m.content]) == 1
        restored.session.close()
        await command(app, "解释刚才的结果")
        await app._agent_task
        assert any("RESULT" in m.content for m in main.main_history[0])
        assert app.agent.client is app.client and app.size.width == size[0]


@pytest.mark.asyncio
async def test_clear_cancels_skill_and_discards_late_result(memory_app, monkeypatch):
    make, _, root = memory_app
    install(root)
    child = Capture(hold=asyncio.Event())
    monkeypatch.setattr("nanocursor.skills.executor.create_client", lambda _: child)
    app = make()
    async with app.run_test() as pilot:
        await command(app, "/review changes")
        await child.entered.wait()
        await command(app, "/clear")
        assert child.closed and child.stream_closed
        assert not app._pending_skill_results
        assert not any("Skill result notification" in m.content for m in app.conversation.history)
        assert not [t for t, (_, kind) in app._owned_tasks.items() if kind == "skill" and not t.done()]


@pytest.mark.asyncio
async def test_result_queue_retries_uncommitted_save_once(memory_app, monkeypatch):
    from nanocursor.skills.executor import SkillRunResult
    make, _, root = memory_app
    app = make()
    async with app.run_test() as pilot:
        app._queue_skill_result(app.session.session_id, app.conversation, "review", SkillRunResult("success", "UNIQUE_RESULT", "offline", "offline"))
        append = app.session.append
        def fail(_):
            raise OSError("disk full")
        monkeypatch.setattr(app.session, "append", fail)
        with pytest.raises(OSError, match="disk full"):
            app._flush_skill_results()
        assert not any("UNIQUE_RESULT" in m.content for m in app.conversation.history)
        assert len(app._pending_skill_results) == 1
        monkeypatch.setattr(app.session, "append", append)
        app._flush_skill_results()
        app._flush_skill_results()
        assert len([m for m in app.conversation.history if "UNIQUE_RESULT" in m.content]) == 1
