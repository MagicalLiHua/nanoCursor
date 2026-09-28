"""End-to-end checkpoint acceptance with real Git and process-loss boundaries.

Child programs use the real storage, file tools, Agent and command handler. The
only substituted component is the model, so no credentials or network are used.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nanocursor.conversation import Message
from nanocursor.filehistory import FileHistory
from nanocursor.memory.session import SessionManager
from nanocursor.permissions.approval_context import AuthorizationContext
from nanocursor.recovery import RecoveryRequired, RecoveryRuntime, RecoveryStore
from nanocursor.tools.write_file import Params, WriteFile


async def _write(history, path, content):
    result = await WriteFile(file_history=history).execute(Params(file_path=str(path), content=content))
    assert not result.is_error, result.output


def _child(program, tmp_path, *args, killed=False):
    result = subprocess.run(
        [sys.executable, "-c", program, *map(str, args)],
        env={**os.environ, "NANOCURSOR_HOME": str(tmp_path / "home")},
        capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == (-signal.SIGKILL if killed else 0), result.stdout + result.stderr
    return result


@pytest.mark.asyncio
async def test_git_dirty_staged_untracked_baseline_and_index_survive_actual_restore(tmp_path):
    work = tmp_path / "project"
    work.mkdir()

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(work), *args],
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )

    git("init", "-q")
    for name in ("staged", "mixed", "unstaged"):
        (work / name).write_text("committed\n")
    git("add", ".")
    git("-c", "user.name=Checkpoint Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline")
    (work / "staged").write_text("user staged\n")
    (work / "mixed").write_text("user staged layer\n")
    git("add", "staged", "mixed")
    (work / "mixed").write_text("user unstaged layer\n")
    (work / "unstaged").write_text("user unstaged\n")
    (work / "untracked").write_text("user untracked\n")
    originals = {name: (work / name).read_bytes() for name in ("staged", "mixed", "unstaged", "untracked")}
    index_before = (work / ".git" / "index").read_bytes()
    status_before = git("status", "--porcelain=v1", "--untracked-files=all")
    cached_before = git("diff", "--cached", "--binary")
    unstaged_before = git("diff", "--binary")
    store = RecoveryStore(tmp_path / "state")
    history = FileHistory(str(work), "test", store=store)
    checkpoint = history.begin_checkpoint(0, "change files", conversation=[])
    for name in (*originals, "agent-created"):
        await _write(history, work / name, "agent replacement\n")
    store.close()

    reopened = RecoveryStore(tmp_path / "state")
    try:
        history = FileHistory(str(work), "test", store=reopened)
        preview = history.preview(checkpoint.checkpoint_id)
        assert not preview.conflicts and len(preview.files) == 5
        history.rewind(checkpoint.checkpoint_id)
        assert {name: (work / name).read_bytes() for name in originals} == originals
        assert not (work / "agent-created").exists()
        assert (work / ".git" / "index").read_bytes() == index_before
        assert git("status", "--porcelain=v1", "--untracked-files=all") == status_before
        assert git("diff", "--cached", "--binary") == cached_before
        assert git("diff", "--binary") == unstaged_before
    finally:
        reopened.close()


FIRST_TASK_PROGRAM = r'''
import asyncio, json, sys
from pathlib import Path
from nanocursor.agent import Agent
from nanocursor.client import LLMClient
from nanocursor.conversation import ConversationManager
from nanocursor.memory.session import SessionManager
from nanocursor.recovery import RecoveryRuntime, RecoveryStore
from nanocursor.tools import ToolRegistry
from nanocursor.tools.base import ToolCallComplete, StreamEnd
from nanocursor.tools.write_file import WriteFile
work,state,info=map(Path,sys.argv[1:])
store=RecoveryStore(state)
runtime=RecoveryRuntime.acquire(work,store=store)
session=SessionManager(str(work),recovery=runtime).create()
class Client(LLMClient):
 def __init__(self): self.calls=0
 async def stream(self,*args,**kwargs):
  self.calls+=1
  if self.calls==1:
   yield ToolCallComplete('write','WriteFile',{'file_path':str(work/'file'),'content':'agent edit'})
   yield StreamEnd('end_turn')
  else:
   raise RuntimeError('model connection failed after committed tool result')
registry=ToolRegistry()
registry.register(WriteFile())
agent=Agent(Client(),registry,'anthropic',str(work),recovery=runtime,inject_environment_context=False)
agent.session_id=session.session_id
conversation=ConversationManager()
conversation.add_user_message('edit the existing file')
async def run():
 try:
  async for event in agent.run(conversation): pass
 except RuntimeError as exc:
  assert 'model connection failed' in str(exc)
 else: raise AssertionError('the model failure was not observed')
asyncio.run(run())
assert (work/'file').read_text()=='agent edit'
points=agent.file_history.get_snapshots()
assert len(points)==1 and points[0].conversation==[]
info.write_text(json.dumps({'session':session.session_id,'checkpoint':points[0].checkpoint_id}))
session.close()
runtime.close()
store.close()
'''


def test_first_agent_write_then_model_failure_can_restore_after_process_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    work, state, info = tmp_path / "project", tmp_path / "state", tmp_path / "info.json"
    work.mkdir()
    (work / "file").write_text("user's existing work")
    _child(FIRST_TASK_PROGRAM, tmp_path, work, state, info)
    identity = json.loads(info.read_text())
    store = RecoveryStore(state)
    runtime = RecoveryRuntime.acquire(work, store=store)
    resumed = SessionManager(str(work), recovery=runtime).resume(identity["session"])
    try:
        assert runtime.pending() == []
        assert store.rows("SELECT state FROM runs")[0]["state"] == "interrupted"
        tool = store.rows("SELECT state,result,is_error FROM operations WHERE kind='tool'")[0]
        assert tool["state"] == "completed" and tool["result"] and not tool["is_error"]
        history = FileHistory(str(work), identity["session"], store=store)
        checkpoint = history.snapshot(identity["checkpoint"])
        assert checkpoint.conversation == []
        assert (work / "file").read_text() == "agent edit"
        runtime.ensure_workspace_idle()
        history.rewind(checkpoint.checkpoint_id)
        assert (work / "file").read_text() == "user's existing work"
    finally:
        resumed.session.close()
        runtime.close()
        store.close()


RESTORE_CRASH_PROGRAM = r'''
import os,signal,sys
from pathlib import Path
from nanocursor.filehistory import FileHistory
from nanocursor.recovery import RecoveryRuntime,RecoveryStore
work,state,restore_id,phase=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3],sys.argv[4]
store=RecoveryStore(state)
runtime=RecoveryRuntime.acquire(work,store=store)
history=FileHistory(str(work),'test',store=store)
def kill(): os.kill(os.getpid(),signal.SIGKILL)
original_replace=os.replace
def replace(src,dst):
 target=Path(dst)
 if phase=='before_first_replace' and target==work/'a': kill()
 if phase=='after_first_progress' and target==work/'b': kill()
 return original_replace(src,dst)
os.replace=replace
def fault(stage):
 if stage!='before_commit': return
 a,b=(work/'a').read_text(),(work/'b').read_text()
 if phase=='after_first_replace' and a=='A' and b=='B': kill()
 if phase=='after_last_replace' and a==b=='A': kill()
store.fault_hook=fault
history.apply_restore(restore_id)
if phase=='after_files_done': kill()
raise AssertionError('requested crash boundary was not reached')
'''


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,observed,item_states,restore_state", [
    ("before_first_replace", ["not_restored", "not_restored"], ["pending", "pending"], "prepared"),
    ("after_first_replace", ["at_target", "not_restored"], ["pending", "pending"], "prepared"),
    ("after_first_progress", ["at_target", "not_restored"], ["applied", "pending"], "prepared"),
    ("after_last_replace", ["at_target", "at_target"], ["applied", "pending"], "prepared"),
    ("after_files_done", ["at_target", "at_target"], ["applied", "applied"], "files_done"),
])
async def test_sigkill_at_each_two_file_restore_boundary_retains_progress_and_safety_copies(
    tmp_path, phase, observed, item_states, restore_state,
):
    work, state = tmp_path / "project", tmp_path / "state"
    work.mkdir()
    store = RecoveryStore(state)
    history = FileHistory(str(work), "test", store=store)
    for name in ("a", "b"):
        (work / name).write_text("A")
    checkpoint = history.begin_checkpoint(0, "two files", conversation=[])
    for name in ("a", "b"):
        await _write(history, work / name, "B")
    restore_id = history.start_restore(checkpoint.checkpoint_id)
    store.close()
    _child(RESTORE_CRASH_PROGRAM, tmp_path, work, state, restore_id, phase, killed=True)

    store = RecoveryStore(state)
    runtime = RecoveryRuntime.acquire(work, store=store)
    try:
        history = FileHistory(str(work), "test", store=store)
        info = history.restore_info(restore_id)
        assert info["state"] == restore_state
        assert [item["observed"] for item in info["items"]] == observed
        assert [item["state"] for item in info["items"]] == item_states
        with pytest.raises(RecoveryRequired):
            runtime.ensure_ready()
        for item in info["items"]:
            assert store.get_blob(json.loads(item["before_state"])["digest"]) == b"B"
        runtime.ensure_workspace_idle(allow_restore_id=restore_id)
        history.apply_restore(restore_id)
        history.complete_restore(restore_id)
        assert [(work / name).read_text() for name in ("a", "b")] == ["A", "A"]
        assert not history.pending_restores()
        assert history.apply_restore(restore_id) == []
        runtime.ensure_ready()
    finally:
        runtime.close()
        store.close()


CONVERSATION_CRASH_PROGRAM = r'''
import asyncio,os,signal,sys
from pathlib import Path
from types import SimpleNamespace as NS
from nanocursor.commands.handlers.rewind import _handle_rewind
from nanocursor.config import ApprovalConfig,ProviderConfig
from nanocursor.conversation import ConversationManager
from nanocursor.filehistory import FileHistory
from nanocursor.memory.session import SessionManager
from nanocursor.permissions.reviewer import ApprovalController
from nanocursor.recovery import RecoveryRuntime,RecoveryStore
work,state,session_id,restore_id,phase=Path(sys.argv[1]),Path(sys.argv[2]),*sys.argv[3:]
store=RecoveryStore(state)
runtime=RecoveryRuntime.acquire(work,store=store)
resumed=SessionManager(str(work),recovery=runtime).resume(session_id)
session=resumed.session
history=FileHistory(str(work),session_id,store=store)
runtime.file_history=history
provider=ProviderConfig('test','anthropic','https://unused.invalid','test')
controller=ApprovalController(ApprovalConfig(),[provider],provider)
controller.authorization=session.load_approval_context()
controller.on_authorization_changed=session.save_approval_context
conversation=ConversationManager()
conversation.replace_history(resumed.messages)
original_reset=session.reset_history
def reset(*args,**kwargs):
 if phase=='before_reset': os.kill(os.getpid(),signal.SIGKILL)
 original_reset(*args,**kwargs)
 if phase=='after_projection': os.kill(os.getpid(),signal.SIGKILL)
session.reset_history=reset
agent=NS(file_history=history,recovery=runtime,approval_controller=controller,
         clear_active_skills=lambda:None,_file_versions={})
ctx=NS(agent=agent,session=session,conversation=conversation,args=f'resume {restore_id} apply',
       ui=NS(add_system_message=print),config={})
asyncio.run(_handle_rewind(ctx))
assert phase=='finish' and not history.pending_restores()
assert not controller.authorization.complete and controller.authorization.records==[]
session.close()
runtime.close()
store.close()
'''


@pytest.mark.asyncio
async def test_two_sigkills_around_conversation_projection_produce_one_logical_rewind(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    work, state = tmp_path / "project", tmp_path / "state"
    work.mkdir()
    store = RecoveryStore(state)
    runtime = RecoveryRuntime.acquire(work, store=store)
    session = SessionManager(str(work), recovery=runtime).create()
    session_id = session.session_id
    history = FileHistory(str(work), session_id, store=store)
    session.append(Message("user", "baseline conversation"))
    authorization = AuthorizationContext()
    authorization.add("old task permission must not return")
    session.save_approval_context(authorization)
    checkpoint = history.begin_checkpoint(1, "change", conversation=[Message("user", "baseline conversation")])
    (work / "file").write_text("A")
    await _write(history, work / "file", "B")
    session.append(Message("user", "later task"))
    restore_id = history.start_restore(checkpoint.checkpoint_id, option=1)
    session.close()
    runtime.close()
    store.close()

    for phase,expected_generation in (("before_reset", 0), ("after_projection", 1), ("finish", 1)):
        _child(CONVERSATION_CRASH_PROGRAM, tmp_path, work, state, session_id, restore_id, phase, killed=phase != "finish")
        store = RecoveryStore(state)
        runtime = RecoveryRuntime.acquire(work, store=store)
        resumed = SessionManager(str(work), recovery=runtime).resume(session_id)
        try:
            history = FileHistory(str(work), session_id, store=store)
            info = history.restore_info(restore_id)
            assert info["state"] == ("complete" if phase == "finish" else "files_done")
            assert (work / "file").read_text() == "A"
            assert runtime.generation == expected_generation
            boundaries = store.rows("SELECT * FROM outbox WHERE record_id=?", (info["conversation_record_id"],))
            assert len(boundaries) == expected_generation
            if phase != "finish":
                with pytest.raises(RecoveryRequired):
                    runtime.ensure_ready()
            if expected_generation:
                assert resumed.messages == [Message("user", "baseline conversation")]
                assert not resumed.session.load_approval_context().complete
            if phase == "finish":
                runtime.ensure_ready()
                assert resumed.session.load_approval_context().records == []
                lines = [json.loads(line) for line in Path(resumed.session._file.name).read_text().splitlines()]
                assert sum(line.get("record_id") == info["conversation_record_id"] for line in lines) == 1
        finally:
            resumed.session.close()
            runtime.close()
            store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_restore", [False, True])
async def test_expired_session_delete_preserves_checkpoint_and_restore_references(tmp_path, pending_restore):
    work = tmp_path / "project"
    work.mkdir()
    store = RecoveryStore(tmp_path / "state")
    runtime = RecoveryRuntime.acquire(work, store=store)
    manager = SessionManager(str(work), recovery=runtime)
    session = manager.create()
    session_id = session.session_id
    history = FileHistory(str(work), session_id, store=store)
    checkpoint = history.begin_checkpoint(0, "retained evidence", conversation=[])
    history.pin_checkpoint(checkpoint.checkpoint_id)
    if pending_restore:
        (work / "file").write_text("A")
        await _write(history, work / "file", "B")
        restore_id = history.start_restore(checkpoint.checkpoint_id)
    session.meta.last_active = datetime.now(timezone.utc) - timedelta(days=90)
    session.meta.save(manager._sessions_dir / f"{session_id}.meta")
    session.close()
    # A second expired session has no execution/checkpoint evidence and remains
    # eligible; retaining the first is not implemented by disabling all cleanup.
    unused = manager.create()
    unused_id = unused.session_id
    unused.meta.last_active = datetime.now(timezone.utc) - timedelta(days=90)
    unused.meta.save(manager._sessions_dir / f"{unused_id}.meta")
    unused.close()
    try:
        assert not manager.delete(session_id)
        assert manager.cleanup(max_age_days=30) == 1
        assert (manager._sessions_dir / f"{session_id}.jsonl").exists()
        assert (manager._sessions_dir / f"{session_id}.meta").exists()
        assert not (manager._sessions_dir / f"{unused_id}.jsonl").exists()
        assert history.snapshot(checkpoint.checkpoint_id).pinned
        if pending_restore:
            info = history.restore_info(restore_id)
            assert info["state"] == "prepared"
            assert store.get_blob(json.loads(info["items"][0]["before_state"])["digest"]) == b"B"
    finally:
        runtime.close()
        store.close()
