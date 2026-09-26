from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from nanocursor.agent import PermissionResponse
from nanocursor.permissions import PermissionMode, Rule
from nanocursor.tools.base import ToolCallComplete
from nanocursor.tools.write_file import WriteFile
from nanocursor.tools.edit_file import EditFile
from test_auto_approval import setup


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["WriteFile", "EditFile"])
async def test_edit_option_switches_existing_mode_without_saving_rules(setup, name):
    s = setup
    s.agent.registry.register(WriteFile())
    s.agent.registry.register(EditFile())
    path = s.root / "file.txt"
    arguments = {"file_path": str(path), "content": "after"}
    if name == "EditFile":
        path.write_text("before")
        arguments = {"file_path": str(path), "old_string": "before", "new_string": "after"}
    prompt = AsyncMock(return_value=PermissionResponse.ALLOW_EDITS)
    revision = s.controller.revision
    result = await s.agent._execute_single_tool_direct(ToolCallComplete("one", name, arguments), prompt)
    assert not result.result.is_error and path.read_text() == "after"
    assert prompt.call_args.args[0].allow_edits
    assert s.agent.permission_mode == s.checker.mode == PermissionMode.ACCEPT_EDITS
    assert s.controller.revision == revision + 1
    assert not s.checker._session_allowed and not (s.root / "permissions.yaml").exists()
    s.complete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [PermissionResponse.ALLOW, PermissionResponse.DENY])
async def test_once_and_no_keep_default_mode(setup, response):
    s = setup
    s.agent.registry.register(WriteFile())
    path = s.root / "file.txt"
    result = await s.agent._execute_single_tool_direct(
        ToolCallComplete("one", "WriteFile", {"file_path": str(path), "content": "after"}),
        AsyncMock(return_value=response))
    assert result.result.is_error == (response == PermissionResponse.DENY)
    assert path.exists() == (response == PermissionResponse.ALLOW)
    assert s.agent.permission_mode == s.checker.mode == PermissionMode.DEFAULT


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["ask", "path", "plan", "child", "bash"])
async def test_restricted_requests_cannot_enable_edit_mode(setup, restriction):
    s = setup
    s.controller.config.mode = "manual"
    s.agent.registry.register(WriteFile())
    path = s.root / (".nanocursor/config.yaml" if restriction == "path" else "file.txt")
    if restriction == "ask":
        s.checker.rule_engine.append_local_rule(Rule("WriteFile", "*", "ask"))
    elif restriction == "plan":
        s.agent.set_permission_mode(PermissionMode.PLAN)
    elif restriction == "child":
        s.agent.parent_id = "parent"
    mode = s.agent.permission_mode
    call = (ToolCallComplete("one", "Bash", {"command": "python -m pytest"}) if restriction == "bash" else
            ToolCallComplete("one", "WriteFile", {"file_path": str(path), "content": "after"}))
    prompt = AsyncMock(return_value=PermissionResponse.ALLOW_EDITS)
    result = await s.agent._execute_single_tool_direct(call, prompt)
    assert not prompt.call_args.args[0].allow_edits
    assert result.result.is_error
    assert s.agent.permission_mode == s.checker.mode == mode
    assert not path.exists() and not s.bash.executed


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["ask", "deny", "mode", "cwd"])
async def test_changed_conditions_invalidate_the_edit_mode_option(setup, change):
    s = setup
    s.agent.registry.register(WriteFile())
    path = s.root / "file.txt"
    async def respond(call):
        assert call.allow_edits
        if change in {"ask", "deny"}:
            s.checker.rule_engine.append_local_rule(Rule("WriteFile", "*", change))
        elif change == "mode":
            s.agent.set_permission_mode(PermissionMode.PLAN)
        else:
            other = s.root / "other"
            other.mkdir()
            s.agent.set_work_dir(str(other))
        return PermissionResponse.ALLOW_EDITS
    result = await s.agent._execute_single_tool_direct(
        ToolCallComplete("one", "WriteFile", {"file_path": str(path), "content": "after"}), respond)
    assert result.result.is_error and "invalidated" in result.result.output
    assert s.agent.permission_mode != PermissionMode.ACCEPT_EDITS
    assert not path.exists()
