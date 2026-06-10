"""Failure recovery loop planning tests."""

from __future__ import annotations

import uuid
import asyncio

from src.api.services.failure_classifier_service import FailureClass
from src.api.services.failure_recovery_loop_service import (
    build_command_failure_evidence,
    build_recovery_plan,
    classify_command_failure,
    execute_recovery_agent_task_async,
    execute_recovery_plan_async,
    get_recovery_loop_state,
    get_recovery_plan,
    plan_latest_failure_recovery,
    prepare_recovery_agent_task,
    stop_recovery_loop,
)


def _thread_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def test_pytest_failure_generates_auto_recovery_plan(tmp_workspace):
    (tmp_workspace / "test_app.py").write_text("def test_add():\n    assert 1 == 2\n", encoding="utf-8")
    evidence = build_command_failure_evidence(
        thread_id=_thread_id("pytest"),
        workspace_dir=str(tmp_workspace),
        tool_result={
            "tool_name": "shell",
            "command": "pytest -q test_app.py",
            "exit_code": 1,
            "stderr": "FAILED test_app.py::test_add - AssertionError: assert 1 == 2",
        },
    )

    failure = classify_command_failure(evidence)
    plan = build_recovery_plan(evidence.thread_id, str(tmp_workspace), failure, evidence)

    assert failure.failure_class == FailureClass.TEST_FAILURE
    assert failure.evidence["recovery_failure_type"] == "pytest_assertion_failure"
    assert plan.can_auto_recover is True
    assert {step.kind for step in plan.steps} >= {"inspect_file", "edit_file", "rerun_command"}
    assert "test_app.py" in evidence.related_files


def test_module_not_found_requires_user_confirmation(tmp_workspace):
    evidence = build_command_failure_evidence(
        thread_id=_thread_id("module"),
        workspace_dir=str(tmp_workspace),
        tool_result={
            "command": "python app.py",
            "exit_code": 1,
            "stderr": "ModuleNotFoundError: No module named 'rich'",
        },
    )

    failure = classify_command_failure(evidence)
    plan = build_recovery_plan(evidence.thread_id, str(tmp_workspace), failure, evidence)

    assert failure.failure_class == FailureClass.ENVIRONMENT_ERROR
    assert failure.evidence["recovery_failure_type"] == "module_not_found"
    assert plan.can_auto_recover is False
    assert any(step.kind == "ask_user" for step in plan.steps)
    assert all("pip install" not in (step.command or "") for step in plan.steps)


def test_policy_or_permission_failure_requires_approval(tmp_workspace):
    evidence = build_command_failure_evidence(
        thread_id=_thread_id("policy"),
        workspace_dir=str(tmp_workspace),
        tool_result={
            "command": "rm -rf build",
            "exit_code": 1,
            "stderr": "blocked by policy: requires approval for shell_risky",
        },
    )

    failure = classify_command_failure(evidence)
    plan = build_recovery_plan(evidence.thread_id, str(tmp_workspace), failure, evidence)

    assert failure.failure_class == FailureClass.TOOL_POLICY_BLOCKED
    assert failure.can_auto_retry is False
    assert plan.can_auto_recover is False
    assert any(step.kind == "ask_approval" and step.requires_approval for step in plan.steps)


def test_timeout_uses_single_retry_budget(tmp_workspace):
    evidence = build_command_failure_evidence(
        thread_id=_thread_id("timeout"),
        workspace_dir=str(tmp_workspace),
        tool_result={
            "command": "pytest -q",
            "timed_out": True,
            "stderr": "command timed out after 30s",
        },
    )

    failure = classify_command_failure(evidence)
    plan = build_recovery_plan(evidence.thread_id, str(tmp_workspace), failure, evidence)

    assert failure.evidence["recovery_failure_type"] == "timeout"
    assert plan.retry_budget == 1
    assert plan.can_auto_recover is True
    assert [step.kind for step in plan.steps] == ["rerun_command"]


def test_command_not_found_stops_auto_recovery(tmp_workspace):
    evidence = build_command_failure_evidence(
        thread_id=_thread_id("cmd"),
        workspace_dir=str(tmp_workspace),
        tool_result={
            "command": "ruff check .",
            "exit_code": 127,
            "stderr": "zsh: command not found: ruff",
        },
    )

    failure = classify_command_failure(evidence)
    plan = build_recovery_plan(evidence.thread_id, str(tmp_workspace), failure, evidence)

    assert failure.evidence["recovery_failure_type"] == "command_not_found"
    assert plan.can_auto_recover is False
    assert [step.kind for step in plan.steps] == ["stop"]


def test_path_not_found_uses_workspace_relocation_plan(tmp_workspace):
    evidence = build_command_failure_evidence(
        thread_id=_thread_id("path"),
        workspace_dir=str(tmp_workspace),
        tool_result={
            "command": "python missing_script.py",
            "exit_code": 2,
            "stderr": "python: can't open file 'missing_script.py': No such file or directory",
        },
    )

    failure = classify_command_failure(evidence)
    plan = build_recovery_plan(evidence.thread_id, str(tmp_workspace), failure, evidence)

    assert failure.failure_class == FailureClass.WORKSPACE_ERROR
    assert failure.evidence["recovery_failure_type"] == "path_not_found"
    assert plan.can_auto_recover is True
    assert [step.kind for step in plan.steps] == ["fallback_tool", "rerun_command"]


def test_plan_latest_failure_persists_state_and_retrieves_plan(tmp_workspace):
    thread_id = _thread_id("persist")
    result = plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "command": "pytest -q",
            "exit_code": 1,
            "stderr": "FAILED test_sample.py::test_case - AssertionError",
        },
    )

    state = get_recovery_loop_state(thread_id, str(tmp_workspace))
    plan = get_recovery_plan(thread_id, result["failure"]["failure_id"], str(tmp_workspace))

    assert state["latest_plan_id"] == result["plan"]["plan_id"]
    assert state["summary"]["plan_count"] == 1
    assert plan is not None
    assert plan["failure_id"] == result["failure"]["failure_id"]


def test_execute_safe_rerun_command_records_success_attempt(tmp_workspace, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "never")
    thread_id = _thread_id("execute")
    planned = plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "command": "echo recovered",
            "timed_out": True,
            "stderr": "command timed out after 1s",
        },
    )

    executed = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))
    attempt = executed["attempt"]

    assert attempt["plan_id"] == planned["plan"]["plan_id"]
    assert attempt["status"] == "succeeded"
    assert attempt["step_results"][0]["kind"] == "rerun_command"
    assert attempt["step_results"][0]["status"] == "succeeded"
    state = get_recovery_loop_state(thread_id, str(tmp_workspace))
    assert state["summary"]["successful_attempt_count"] == 1


def test_execute_stops_at_agent_required_edit_step(tmp_workspace):
    (tmp_workspace / "test_app.py").write_text("def test_add():\n    assert 1 == 2\n", encoding="utf-8")
    thread_id = _thread_id("edit_wait")
    plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "command": "pytest -q test_app.py",
            "exit_code": 1,
            "stderr": "FAILED test_app.py::test_add - AssertionError: assert 1 == 2",
        },
    )

    executed = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))
    attempt = executed["attempt"]

    assert attempt["status"] == "waiting_agent"
    assert [step["kind"] for step in attempt["step_results"]] == ["inspect_file", "edit_file"]
    assert attempt["step_results"][1]["status"] == "waiting_agent"
    task_result = attempt["step_results"][1]["result"]
    assert task_result["task_id"].startswith("task-recovery-edit-")
    assert task_result["task_board"]["task"]["type"] == "recovery"
    assert task_result["task_board"]["task"]["agent_role"] == "coder"
    assert task_result["task_board"]["task"]["writes_files"] is True
    assert task_result["task_board"]["task"]["context_policy"]["mode"] == "failure_recovery"
    assert task_result["package"]["task_id"] == task_result["task_id"]
    assert task_result["package"]["validation_command"] == "pytest -q test_app.py"
    assert "Coder Recovery Agent" in task_result["package"]["system"]
    assert "不安装依赖" in task_result["package"]["prompt"]


def test_recovery_edit_step_records_agent_loop_waiting_step(tmp_workspace):
    from src.api.services.agent_loop_state_service import init_agent_loop_state, load_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    (tmp_workspace / "test_app.py").write_text("def test_add():\n    assert 1 == 2\n", encoding="utf-8")
    thread_id = _thread_id("loop_repair")
    init_agent_loop_state(
        thread_id,
        str(tmp_workspace),
        user_request="帮我修复测试失败",
        intent=classify_user_intent("帮我修复测试失败"),
    )
    plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "command": "pytest -q test_app.py",
            "exit_code": 1,
            "stderr": "FAILED test_app.py::test_add - AssertionError: assert 1 == 2",
        },
    )

    executed = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))
    task_result = executed["attempt"]["step_results"][1]["result"]
    state = load_agent_loop_state(thread_id, str(tmp_workspace))

    assert "error" not in task_result["loop_step"]
    assert state is not None
    assert state.terminal_status is None
    assert state.steps[-1].phase == "recover"
    assert state.steps[-1].status == "completed"
    assert state.steps[-1].action.task_id == task_result["task_id"]


def test_prepare_recovery_agent_task_marks_task_running_and_persists_package(tmp_workspace):
    from pathlib import Path

    (tmp_workspace / "test_app.py").write_text("def test_add():\n    assert 1 == 2\n", encoding="utf-8")
    thread_id = _thread_id("prepare_repair")
    plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "command": "pytest -q test_app.py",
            "exit_code": 1,
            "stderr": "FAILED test_app.py::test_add - AssertionError: assert 1 == 2",
        },
    )
    executed = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))
    task_id = executed["attempt"]["step_results"][1]["result"]["task_id"]

    package = prepare_recovery_agent_task(thread_id, str(tmp_workspace), task_id=task_id)
    package_path = Path(package["package_path"])

    assert package["task"]["status"] == "running"
    assert package["context_pack_id"]
    assert package["selected_files"]
    assert package_path.exists()
    assert package_path.read_text(encoding="utf-8")


def test_execute_recovery_agent_task_with_runner_marks_task_passed(tmp_workspace, monkeypatch):
    from src.api.services.run_state_service import get_or_create_run_state

    monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "never")
    (tmp_workspace / "test_app.py").write_text("def test_add():\n    assert 1 == 2\n", encoding="utf-8")
    thread_id = _thread_id("run_repair")
    plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "command": "pytest -q test_app.py",
            "exit_code": 1,
            "stderr": "FAILED test_app.py::test_add - AssertionError: assert 1 == 2",
        },
    )
    executed = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))
    task_id = executed["attempt"]["step_results"][1]["result"]["task_id"]

    async def fake_runner(prompt, system, agent_type, tools):
        tool_names = {tool.get("name") for tool in tools}
        assert "不安装依赖" in prompt
        assert "Coder Recovery Agent" in system
        assert agent_type == "CoderRecovery"
        assert {"read_file", "edit_file", "run_tests"}.issubset(tool_names)
        assert "bash" not in tool_names
        (tmp_workspace / "test_app.py").write_text("def test_add():\n    assert 1 == 1\n", encoding="utf-8")
        return "\n".join([
            "- summary: 已修复断言测试失败。",
            "- changed_files: test_app.py",
            "- validation: pytest -q test_app.py",
            "- blocked: 无",
        ])

    result = asyncio.run(
        execute_recovery_agent_task_async(
            thread_id,
            str(tmp_workspace),
            task_id=task_id,
            runner=fake_runner,
        )
    )
    board = get_or_create_run_state(thread_id, str(tmp_workspace))
    task = board.task(task_id)
    state = get_recovery_loop_state(thread_id, str(tmp_workspace))

    assert result["run"]["status"] == "passed"
    assert result["run"]["validation_result"]["result"] == "success"
    assert result["next_action"] is None
    assert task is not None
    assert task.status == "passed"
    assert any(item.get("kind") == "recovery_agent_output" for item in task.outputs)
    assert state["status"] == "agent_repair_passed"
    assert state["latest_agent_task_run_id"] == result["run"]["run_id"]


def test_successful_recovery_advances_source_failed_task(tmp_workspace, monkeypatch):
    from src.api.services.run_state_service import get_or_create_run_state, patch_run_state
    from src.runtime.task_board import save_task_board
    from src.api.services.event_store import get_event_store

    monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "never")
    (tmp_workspace / "test_app.py").write_text("def test_add():\n    assert 1 == 2\n", encoding="utf-8")
    thread_id = _thread_id("source_repair")
    patch_run_state(
        thread_id,
        str(tmp_workspace),
        {
            "reason": "seed_failed_task",
            "add_or_update_tasks": [
                {
                    "id": "task-test",
                    "type": "test",
                    "title": "运行 pytest",
                    "goal": "验证测试是否通过",
                    "agent_role": "tester",
                    "can_parallel": False,
                    "writes_files": False,
                }
            ],
        },
    )
    board = get_or_create_run_state(thread_id, str(tmp_workspace))
    board.apply_task_status("task-test", "failed")
    save_task_board(board, get_event_store().run_dir(thread_id, str(tmp_workspace)))

    plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "task_id": "task-test",
            "command": "pytest -q test_app.py",
            "exit_code": 1,
            "stderr": "FAILED test_app.py::test_add - AssertionError: assert 1 == 2",
        },
    )
    executed = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))
    task_id = executed["attempt"]["step_results"][1]["result"]["task_id"]

    async def fake_runner(prompt, system, agent_type, tools):
        (tmp_workspace / "test_app.py").write_text("def test_add():\n    assert 1 == 1\n", encoding="utf-8")
        return "\n".join([
            "- summary: 已修复原始失败任务。",
            "- changed_files: test_app.py",
            "- validation: pytest -q test_app.py",
            "- blocked: 无",
        ])

    result = asyncio.run(
        execute_recovery_agent_task_async(
            thread_id,
            str(tmp_workspace),
            task_id=task_id,
            runner=fake_runner,
        )
    )
    board = get_or_create_run_state(thread_id, str(tmp_workspace))
    source_task = board.task("task-test")
    recovery_task = board.task(task_id)

    assert result["run"]["status"] == "passed"
    assert result["package"]["source_task_id"] == "task-test"
    assert result["task_result"]["advanced_source_tasks"][0]["task_id"] == "task-test"
    assert source_task is not None
    assert source_task.status == "passed"
    assert recovery_task is not None
    assert recovery_task.status == "passed"
    assert any(item.get("kind") == "recovered_by_failure_recovery" for item in source_task.evidence)


def test_execute_recovery_agent_task_marks_failed_when_validation_fails(tmp_workspace, monkeypatch):
    from src.api.services.run_state_service import get_or_create_run_state

    monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "never")
    (tmp_workspace / "test_app.py").write_text("def test_add():\n    assert 1 == 2\n", encoding="utf-8")
    thread_id = _thread_id("run_repair_validation_fail")
    planned = plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "command": "pytest -q test_app.py",
            "exit_code": 1,
            "stderr": "FAILED test_app.py::test_add - AssertionError: assert 1 == 2",
        },
    )
    executed = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))
    task_id = executed["attempt"]["step_results"][1]["result"]["task_id"]

    async def fake_runner(prompt, system, agent_type, tools):
        return "\n".join([
            "- summary: 声称已修复，但实际没有改文件。",
            "- changed_files: test_app.py",
            "- validation: pytest -q test_app.py",
            "- blocked: 无",
        ])

    result = asyncio.run(
        execute_recovery_agent_task_async(
            thread_id,
            str(tmp_workspace),
            task_id=task_id,
            runner=fake_runner,
        )
    )
    board = get_or_create_run_state(thread_id, str(tmp_workspace))
    task = board.task(task_id)
    state = get_recovery_loop_state(thread_id, str(tmp_workspace))

    assert result["run"]["status"] == "failed"
    assert result["run"]["validation_result"]["result"] == "failure"
    assert result["next_action"]["type"] == "replan_recovery"
    assert result["next_action"]["replanned"] is True
    assert result["next_action"]["new_plan_id"] != planned["plan"]["plan_id"]
    assert result["next_action"]["new_failure_id"]
    assert task is not None
    assert task.status == "failed"
    assert state["status"] == "agent_repair_failed"
    assert state["latest_plan_id"] == result["next_action"]["new_plan_id"]
    assert state["plans"][-1]["failure_type"] == "pytest_assertion_failure"


def test_execute_recovery_agent_task_records_runner_failure(tmp_workspace):
    from src.api.services.run_state_service import get_or_create_run_state

    (tmp_workspace / "test_app.py").write_text("def test_add():\n    assert 1 == 2\n", encoding="utf-8")
    thread_id = _thread_id("run_repair_fail")
    plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "command": "pytest -q test_app.py",
            "exit_code": 1,
            "stderr": "FAILED test_app.py::test_add - AssertionError: assert 1 == 2",
        },
    )
    executed = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))
    task_id = executed["attempt"]["step_results"][1]["result"]["task_id"]

    async def failing_runner(prompt, system, agent_type, tools):
        raise RuntimeError("model unavailable")

    result = asyncio.run(
        execute_recovery_agent_task_async(
            thread_id,
            str(tmp_workspace),
            task_id=task_id,
            runner=failing_runner,
        )
    )
    board = get_or_create_run_state(thread_id, str(tmp_workspace))
    task = board.task(task_id)
    state = get_recovery_loop_state(thread_id, str(tmp_workspace))

    assert result["run"]["status"] == "failed"
    assert "model unavailable" in result["run"]["error"]
    assert result["next_action"] is None
    assert task is not None
    assert task.status == "failed"
    assert state["status"] == "agent_repair_failed"


def test_execute_high_risk_rerun_waits_for_approval(tmp_workspace):
    thread_id = _thread_id("approval")
    plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "command": "rm -rf build",
            "timed_out": True,
            "stderr": "command timed out after 1s",
        },
    )

    executed = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))
    attempt = executed["attempt"]

    assert attempt["status"] == "waiting_approval"
    assert attempt["step_results"][0]["kind"] == "rerun_command"
    assert attempt["step_results"][0]["status"] == "waiting_approval"
    assert "需要审批" in attempt["step_results"][0]["message"]


def test_execute_stops_when_retry_budget_is_exhausted(tmp_workspace, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "never")
    thread_id = _thread_id("budget")
    plan_latest_failure_recovery(
        thread_id,
        str(tmp_workspace),
        tool_result={
            "command": "echo recovered",
            "timed_out": True,
            "stderr": "command timed out after 1s",
        },
    )

    first = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))
    second = asyncio.run(execute_recovery_plan_async(thread_id, str(tmp_workspace)))

    assert first["attempt"]["status"] == "succeeded"
    assert second["attempt"]["status"] == "stopped"
    assert "预算" in second["attempt"]["stop_reason"]


def test_stop_recovery_loop_persists_stopped_state(tmp_workspace):
    thread_id = _thread_id("stop")
    state = stop_recovery_loop(thread_id, str(tmp_workspace), reason="manual stop")

    assert state["status"] == "stopped"
    assert state["stop_reason"] == "manual stop"
