"""Deterministic runtime probes for aggregate Agent Eval sections."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from src.api.services.context_service import build_context_pack
from src.api.services.conversation_service import (
    create_conversation,
    link_run_to_conversation,
    list_conversation_runs,
    list_conversations,
)
from src.api.services.event_store import get_event_store


def run_runtime_context_section(workspace: Path) -> dict[str, Any]:
    cases = [
        _probe_context_selection_accuracy(workspace),
        _probe_workspace_scope_isolation(workspace),
        _probe_recovery_context_injection(workspace),
    ]
    return _section_result(
        "runtime_context",
        "Context And Isolation",
        "上下文选择、工作区隔离和失败恢复上下文探针。",
        cases,
    )


def run_runtime_memory_section(workspace: Path) -> dict[str, Any]:
    cases = [
        _probe_memory_precision(workspace),
        _probe_stale_memory_blocked(workspace),
        _probe_memory_scope_isolation(workspace),
        _probe_followup_memory_hit(workspace),
    ]
    return _section_result(
        "runtime_memory",
        "Memory Governance",
        "记忆相关性、陈旧拦截、作用域隔离和连续对话召回评测。",
        cases,
    )


def run_runtime_delivery_section(workspace: Path) -> dict[str, Any]:
    cases = [
        _probe_small_edit_rejects_claim_only(workspace),
        _probe_small_edit_accepts_change_evidence(workspace),
        _probe_small_edit_blocks_risky_shell(workspace),
    ]
    return _section_result(
        "runtime_delivery",
        "Runtime Loop Delivery",
        "Controller Loop、small edit 交付证据和高风险动作边界探针。",
        cases,
    )


def _probe_context_selection_accuracy(workspace: Path) -> dict[str, Any]:
    probe = _make_probe_workspace(workspace, "context-selection")
    pack = build_context_pack(
        prompt="修复 app.py 里的 add 函数并运行测试",
        workspace_dir=str(probe),
        execution_plan={
            "strategy": "bug_fix",
            "stages": [
                {"id": "inspect", "title": "定位失败文件"},
                {"id": "verify", "title": "运行 pytest"},
            ],
        },
    )
    data = pack.to_dict()
    selected = data.get("selected_files") if isinstance(data.get("selected_files"), list) else []
    app_item = next((item for item in selected if item.get("path") == "app.py"), None)
    checks = [
        _case_check("selected_app_py", app_item is not None, [item.get("path") for item in selected], "app.py"),
        _case_check("has_reasons", bool(app_item and app_item.get("reasons")), app_item, "non-empty reasons"),
        _case_check("budget_included", app_item and app_item.get("budget_decision") == "included", app_item, "included"),
        _case_check("selection_reasons", bool(data.get("selection_reasons")), data.get("selection_reasons"), "non-empty"),
        _case_check(
            "p0_preserved",
            data.get("context_debug", {}).get("protected_context", {}).get("preserved") is True,
            data.get("context_debug", {}).get("protected_context"),
            True,
        ),
    ]
    return _probe_result("context_selection_accuracy", checks)


def _probe_workspace_scope_isolation(workspace: Path) -> dict[str, Any]:
    left = _make_probe_workspace(workspace, "scope-left")
    right = _make_probe_workspace(workspace, "scope-right")
    left_conversation = create_conversation("左侧工作区任务", str(left))
    right_conversation = create_conversation("右侧工作区任务", str(right))
    left_id = left_conversation["conversation_id"]
    right_id = right_conversation["conversation_id"]
    link_run_to_conversation(left_id, "left-run-1", str(left), prompt="只属于左侧")
    link_run_to_conversation(right_id, "right-run-1", str(right), prompt="只属于右侧")

    left_runs = list_conversation_runs(left_id, str(left)) or {}
    right_runs = list_conversation_runs(right_id, str(right)) or {}
    left_list = list_conversations(str(left), limit=20)
    right_list = list_conversations(str(right), limit=20)
    checks = [
        _case_check(
            "left_run_scoped",
            [run.get("thread_id") for run in left_runs.get("runs", [])] == ["left-run-1"],
            left_runs.get("runs"),
            "left-run-1 only",
        ),
        _case_check(
            "right_run_scoped",
            [run.get("thread_id") for run in right_runs.get("runs", [])] == ["right-run-1"],
            right_runs.get("runs"),
            "right-run-1 only",
        ),
        _case_check(
            "left_list_isolated",
            right_id not in {item.get("conversation_id") for item in left_list},
            [item.get("conversation_id") for item in left_list],
            f"no {right_id}",
        ),
        _case_check(
            "right_list_isolated",
            left_id not in {item.get("conversation_id") for item in right_list},
            [item.get("conversation_id") for item in right_list],
            f"no {left_id}",
        ),
    ]
    return _probe_result("workspace_scope_isolation", checks)


def _probe_recovery_context_injection(workspace: Path) -> dict[str, Any]:
    probe = _make_probe_workspace(workspace, "recovery-context")
    thread_id = f"agent-eval-recovery-{int(time.time() * 1000)}"
    store = get_event_store()
    store.create_session(thread_id, "修复测试失败", str(probe), status="failed")
    store.append_event(
        thread_id,
        "error",
        title="pytest failed",
        content="FAILED test_app.py::test_add - AssertionError: expected add(1, 2) == 4",
        agent="tester",
        payload={"task_id": "verify", "error": "test_app.py failed"},
        workspace_dir=str(probe),
    )
    pack = build_context_pack(
        prompt="继续修复刚才的测试失败",
        workspace_dir=str(probe),
        thread_id=thread_id,
        execution_plan={"strategy": "bug_fix", "stages": [{"id": "verify", "title": "验证"}]},
    )
    data = pack.to_dict()
    failures = data.get("recent_failures") if isinstance(data.get("recent_failures"), list) else []
    related_files = {
        path
        for failure in failures
        for path in failure.get("related_files", [])
        if isinstance(failure, dict)
    }
    selected_paths = [item.get("path") for item in data.get("selected_files", []) if isinstance(item, dict)]
    checks = [
        _case_check("failure_included", bool(failures), failures, "non-empty recent_failures"),
        _case_check("related_file_extracted", "test_app.py" in related_files, sorted(related_files), "test_app.py"),
        _case_check("related_file_selected", "test_app.py" in selected_paths, selected_paths, "test_app.py"),
        _case_check(
            "debug_counts_related_file",
            data.get("context_debug", {}).get("failure_context", {}).get("related_file_count", 0) >= 1,
            data.get("context_debug", {}).get("failure_context"),
            "related_file_count >= 1",
        ),
    ]
    return _probe_result("recovery_context_injection", checks)


def _probe_memory_precision(workspace: Path) -> dict[str, Any]:
    from src.api.services.memory_governance_service import create_memory_record
    from src.api.services.memory_selection_service import select_memories

    probe = _make_probe_workspace(workspace, "memory-precision")
    relevant = create_memory_record(
        str(probe),
        scope="workspace",
        kind="workflow_note",
        content="Parser timeout fixes must run parser regression tests.",
        source="user",
        confidence=0.95,
        importance=8,
    )
    irrelevant = create_memory_record(
        str(probe),
        scope="workspace",
        kind="workflow_note",
        content="The marketing website uses a blue illustration.",
        source="user",
        confidence=0.95,
        importance=8,
    )
    result = select_memories(
        str(probe),
        prompt="Fix the parser timeout and run parser regression tests",
        persist_audit=False,
    )
    selected_ids = {item.get("id") for item in result.get("selected", [])}
    omitted = {item.get("id"): item.get("reason") for item in result.get("omitted", [])}
    checks = [
        _case_check("relevant_selected", relevant["id"] in selected_ids, sorted(selected_ids), relevant["id"]),
        _case_check("irrelevant_omitted", irrelevant["id"] not in selected_ids, sorted(selected_ids), f"no {irrelevant['id']}"),
        _case_check("omission_explained", "low relevance" in str(omitted.get(irrelevant["id"], "")), omitted.get(irrelevant["id"]), "low relevance"),
    ]
    return _probe_result("memory_precision", checks)


def _probe_stale_memory_blocked(workspace: Path) -> dict[str, Any]:
    from src.api.services.memory_governance_service import create_memory_record
    from src.api.services.memory_selection_service import select_memories

    probe = _make_probe_workspace(workspace, "memory-stale")
    target = probe / "app.py"
    memory = create_memory_record(
        str(probe),
        scope="file",
        file_path="app.py",
        kind="project_fact",
        content="app.py add returns the sum of two values.",
        source="user",
        confidence=0.95,
        importance=8,
    )
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    result = select_memories(
        str(probe),
        prompt="Inspect app.py add",
        selected_files=["app.py"],
        persist_audit=False,
    )
    selected_ids = {item.get("id") for item in result.get("selected", [])}
    omitted = {item.get("id"): item.get("reason") for item in result.get("omitted", [])}
    checks = [
        _case_check("stale_not_selected", memory["id"] not in selected_ids, sorted(selected_ids), f"no {memory['id']}"),
        _case_check("stale_reason", "not selectable" in str(omitted.get(memory["id"], "")), omitted.get(memory["id"]), "not selectable"),
    ]
    return _probe_result("stale_memory_blocked", checks)


def _probe_memory_scope_isolation(workspace: Path) -> dict[str, Any]:
    from src.api.services.memory_governance_service import create_memory_record
    from src.api.services.memory_selection_service import select_memories

    probe = _make_probe_workspace(workspace, "memory-scope")
    left = create_memory_record(
        str(probe),
        scope="conversation",
        conversation_id="conversation-left",
        kind="decision",
        content="Continue by running the left parser regression suite.",
        source="user",
        confidence=0.95,
        importance=8,
    )
    right = create_memory_record(
        str(probe),
        scope="conversation",
        conversation_id="conversation-right",
        kind="decision",
        content="Continue by running the right parser regression suite.",
        source="user",
        confidence=0.95,
        importance=8,
    )
    result = select_memories(
        str(probe),
        prompt="Continue the parser regression work",
        conversation_id="conversation-left",
        persist_audit=False,
    )
    selected_ids = {item.get("id") for item in result.get("selected", [])}
    omitted = {item.get("id"): item.get("reason") for item in result.get("omitted", [])}
    checks = [
        _case_check("matching_conversation_selected", left["id"] in selected_ids, sorted(selected_ids), left["id"]),
        _case_check("other_conversation_omitted", right["id"] not in selected_ids, sorted(selected_ids), f"no {right['id']}"),
        _case_check("scope_mismatch_explained", omitted.get(right["id"]) == "conversation scope mismatch", omitted.get(right["id"]), "conversation scope mismatch"),
    ]
    return _probe_result("memory_scope_isolation", checks)


def _probe_followup_memory_hit(workspace: Path) -> dict[str, Any]:
    from src.api.services.memory_governance_service import create_memory_record
    from src.api.services.memory_selection_service import select_memories

    probe = _make_probe_workspace(workspace, "memory-followup")
    memory = create_memory_record(
        str(probe),
        scope="conversation",
        conversation_id="followup-conversation",
        kind="decision",
        content="用户说继续时，下一步运行 pytest parser regression tests。",
        source="user",
        confidence=0.95,
        importance=8,
    )
    result = select_memories(
        str(probe),
        prompt="继续",
        conversation_id="followup-conversation",
        persist_audit=False,
    )
    selected = next((item for item in result.get("selected", []) if item.get("id") == memory["id"]), None)
    checks = [
        _case_check("followup_selected", selected is not None, selected, memory["id"]),
        _case_check("selection_scored", bool(selected and selected.get("score")), selected, "score > 0"),
        _case_check("selection_explained", bool(selected and selected.get("reasons")), selected, "non-empty reasons"),
    ]
    return _probe_result("followup_memory_hit", checks)


def _probe_small_edit_rejects_claim_only(workspace: Path) -> dict[str, Any]:
    from src.api.services.runtime_evidence_service import collect_runtime_delivery_evidence

    probe = _make_probe_workspace(workspace, "small-edit-claim-only")
    thread_id = f"agent-eval-claim-only-{int(time.time() * 1000)}"
    get_event_store().create_session(thread_id, "帮我改 README 的错别字", str(probe), status="running")
    evidence = collect_runtime_delivery_evidence(
        thread_id,
        str(probe),
        tool_calls=[],
    )
    checks = [
        _case_check("not_ready", evidence.ready is False, evidence.ready, False),
        _case_check("no_write_action", evidence.has_write_action is False, evidence.has_write_action, False),
        _case_check("reason_is_explicit", "未检测到本轮成功写入工具调用" in evidence.reason, evidence.reason, "未检测到本轮成功写入工具调用"),
    ]
    return _probe_result("small_edit_rejects_claim_only", checks)


def _probe_small_edit_accepts_change_evidence(workspace: Path) -> dict[str, Any]:
    from src.api.services.runtime_evidence_service import collect_runtime_delivery_evidence

    probe = _make_probe_workspace(workspace, "small-edit-change")
    thread_id = f"agent-eval-change-{int(time.time() * 1000)}"
    store = get_event_store()
    store.create_session(thread_id, "帮我改 README 的错别字", str(probe), status="running")
    store.append_event(
        thread_id,
        "file_changed",
        title="文件变更：README.md",
        content="updated",
        agent="coder",
        payload={"path": "README.md", "change_type": "modified", "tool": "write_file"},
        workspace_dir=str(probe),
    )
    store.append_event(
        thread_id,
        "diff_updated",
        title="Diff 已更新",
        content="1 个文件发生变化",
        agent="coder",
        payload={"changed_files": [{"path": "README.md", "status": "event"}], "source": "events"},
        workspace_dir=str(probe),
    )
    evidence = collect_runtime_delivery_evidence(
        thread_id,
        str(probe),
        tool_calls=[{"tool": "write_file", "input": {"path": "README.md"}, "ok": True}],
    )
    checks = [
        _case_check("ready", evidence.ready is True, evidence.ready, True),
        _case_check("has_changes", evidence.has_changes is True, evidence.has_changes, True),
        _case_check("has_verification", evidence.has_verification is True, evidence.has_verification, True),
    ]
    return _probe_result("small_edit_accepts_change_evidence", checks)


def _probe_small_edit_blocks_risky_shell(workspace: Path) -> dict[str, Any]:
    from src.api.services.agent_loop_state_service import check_loop_tool_guard, init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    probe = _make_probe_workspace(workspace, "small-edit-risky-shell")
    thread_id = f"agent-eval-risky-shell-{int(time.time() * 1000)}"
    intent = classify_user_intent("帮我改 README 的错别字")
    init_agent_loop_state(
        thread_id,
        str(probe),
        user_request="帮我改 README 的错别字",
        intent=intent,
    )
    decision = check_loop_tool_guard(thread_id, str(probe), "bash", {"command": "rm -rf build"})
    checks = [
        _case_check("guard_exists", decision is not None, bool(decision), True),
        _case_check("guard_denies", bool(decision and not decision.allowed), decision.allowed if decision else None, False),
        _case_check("permission_is_risky", getattr(decision, "permission_level", "") == "shell_risky", getattr(decision, "permission_level", ""), "shell_risky"),
    ]
    return _probe_result("small_edit_blocks_risky_shell", checks)


def _section_result(section_id: str, label: str, summary: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    passed_count = sum(1 for case in cases if case["status"] == "passed")
    failed_count = len(cases) - passed_count
    return {
        "id": section_id,
        "label": label,
        "status": "passed" if failed_count == 0 else "failed",
        "total": len(cases),
        "passed": passed_count,
        "failed": failed_count,
        "pass_rate": round(passed_count / max(len(cases), 1), 3),
        "cases": cases,
        "summary": summary,
    }


def _make_probe_workspace(workspace: Path, name: str) -> Path:
    probe = workspace / ".nanocursor" / "eval_probe_workspaces" / f"{name}-{int(time.time() * 1000)}"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "app.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n",
        encoding="utf-8",
    )
    (probe / "test_app.py").write_text(
        "from app import add\n\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    (probe / "README.md").write_text("# probe workspace\n", encoding="utf-8")
    return probe


def _case_check(check_id: str, ok: bool, actual: Any, expected: Any) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": "passed" if ok else "failed",
        "actual": actual,
        "expected": expected,
    }


def _probe_result(case_id: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [check for check in checks if check["status"] != "passed"]
    return {
        "id": case_id,
        "status": "passed" if not failed else "failed",
        "checks": checks,
    }
