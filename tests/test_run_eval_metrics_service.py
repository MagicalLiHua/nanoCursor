from __future__ import annotations

import json


def _write_context_pack(workspace, thread_id: str, pack: dict):
    path = workspace / ".nanocursor" / "runs" / thread_id / "context" / "packs" / f"{pack['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")


def test_direct_answer_metrics_do_not_penalize_missing_tools(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.run_eval_metrics_service import build_run_eval_metrics

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = get_event_store()
    thread_id = "metrics-direct"
    store.create_session(thread_id, "哈喽", str(workspace), status="completed")
    store.update_session(thread_id, str(workspace), execution_plan={"strategy": "lead_direct_reply"})
    for index in range(2):
        store.append_event(
            thread_id,
            "loop_turn_finished",
            payload={"turn_id": f"turn-{index}", "step": index + 1},
            workspace_dir=str(workspace),
        )

    result = build_run_eval_metrics(thread_id, str(workspace))

    assert result["overall_status"] == "passed"
    assert result["metrics"]["turn_count"]["value"] == 2
    assert result["metrics"]["turn_count"]["expected_max"] == 2
    assert result["metrics"]["tool_execution_rate"]["status"] == "not_applicable"
    assert result["metrics"]["tool_execution_rate"]["value"] is None


def test_metrics_use_actual_file_and_tool_evidence(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.run_eval_metrics_service import build_run_eval_metrics

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = get_event_store()
    thread_id = "metrics-evidence"
    store.create_session(thread_id, "修复 app.py", str(workspace), status="completed")
    store.update_session(thread_id, str(workspace), execution_plan={"strategy": "small_edit"})
    _write_context_pack(
        workspace,
        thread_id,
        {
            "id": "pack-evidence",
            "selected_files": [{"path": "app.py", "reasons": ["prompt matched app"]}],
            "selected_memories": [],
        },
    )
    store.append_event(
        thread_id,
        "tool_call_finished",
        payload={"tool": "edit_file", "input": {"path": "app.py"}, "output": "updated", "ok": True},
        workspace_dir=str(workspace),
    )
    store.append_event(
        thread_id,
        "tool_call_failed",
        payload={"tool": "bash", "output": "Error: failed"},
        workspace_dir=str(workspace),
    )
    store.append_event(
        thread_id,
        "file_changed",
        payload={"path": "app.py"},
        workspace_dir=str(workspace),
    )

    result = build_run_eval_metrics(thread_id, str(workspace))

    assert result["metrics"]["context_relevance"]["value"] == 1.0
    assert result["metrics"]["context_relevance"]["evidence"]["overlap"] == ["app.py"]
    assert result["metrics"]["tool_execution_rate"]["value"] == 0.5
    assert result["metrics"]["tool_execution_rate"]["evidence"]["successes"] == 1


def test_context_relevance_normalizes_absolute_workspace_paths(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.run_eval_metrics_service import build_run_eval_metrics

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    store = get_event_store()
    thread_id = "metrics-absolute-path"
    store.create_session(thread_id, "查看 src/app.py", str(workspace), status="completed")
    _write_context_pack(
        workspace,
        thread_id,
        {
            "id": "pack-absolute-path",
            "selected_files": [{"path": "src/app.py", "reasons": ["prompt matched app"]}],
            "selected_memories": [],
        },
    )
    store.append_event(
        thread_id,
        "tool_call_finished",
        payload={"tool": "read_file", "input": {"path": str(target)}, "output": "value = 1", "ok": True},
        workspace_dir=str(workspace),
    )

    result = build_run_eval_metrics(thread_id, str(workspace))

    assert result["metrics"]["context_relevance"]["value"] == 1.0
    assert result["metrics"]["context_relevance"]["evidence"]["evidence_files"] == ["src/app.py"]


def test_recovery_metric_requires_original_failure_context(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.run_eval_metrics_service import build_run_eval_metrics

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = get_event_store()
    store.create_session("original-failed", "修复失败", str(workspace), status="failed")
    store.create_session("retry-completed", "继续修复", str(workspace), status="completed", mode="retry")
    store.update_session(
        "retry-completed",
        str(workspace),
        original_thread_id="original-failed",
        original_status="failed",
        retry_context={"failure": {"failure_id": "failure-1"}},
    )
    _write_context_pack(
        workspace,
        "retry-completed",
        {
            "id": "pack-retry",
            "selected_files": [],
            "selected_memories": [],
            "recovery_context": {"original_thread_id": "original-failed"},
        },
    )

    result = build_run_eval_metrics("retry-completed", str(workspace))

    assert result["metrics"]["recovery_success_rate"]["value"] == 1.0
    assert result["metrics"]["recovery_success_rate"]["status"] == "passed"
    assert result["metrics"]["recovery_success_rate"]["evidence"]["retry_context_pack_ids"] == ["pack-retry"]


def test_memory_and_approval_metrics_are_explainable(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.run_eval_metrics_service import build_run_eval_metrics

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = get_event_store()
    thread_id = "metrics-memory-approval"
    store.create_session(thread_id, "继续修复", str(workspace), status="completed")
    _write_context_pack(
        workspace,
        thread_id,
        {
            "id": "pack-memory",
            "selected_files": [],
            "selected_memories": [
                {"id": "local", "scope": "conversation", "reasons": ["conversation scope"]},
                {"id": "broad", "scope": "workspace", "reasons": ["workspace scope"]},
            ],
        },
    )
    store.append_event(thread_id, "approval_requested", workspace_dir=str(workspace))
    store.append_event(thread_id, "approval_resolved", workspace_dir=str(workspace))

    result = build_run_eval_metrics(thread_id, str(workspace))

    assert result["metrics"]["memory_precision"]["value"] == 0.5
    assert result["metrics"]["memory_precision"]["evidence"]["relevant_ids"] == ["local"]
    assert result["metrics"]["approval_resolution_rate"]["value"] == 1.0


def test_approval_metric_does_not_count_wrong_resolution(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.run_eval_metrics_service import build_run_eval_metrics

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = get_event_store()
    thread_id = "metrics-approval-mismatch"
    store.create_session(thread_id, "approve", str(workspace), status="completed")
    store.append_event(thread_id, "approval_requested", payload={"approval_id": "approval-a"}, workspace_dir=str(workspace))
    store.append_event(thread_id, "approval_resolved", payload={"approval_id": "approval-b"}, workspace_dir=str(workspace))

    result = build_run_eval_metrics(thread_id, str(workspace))

    assert result["metrics"]["approval_resolution_rate"]["value"] == 0.0
    assert result["metrics"]["approval_resolution_rate"]["evidence"]["matched"] == 0


def test_workspace_eval_metrics_aggregate_applicable_values(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.run_eval_metrics_service import build_workspace_eval_metrics

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = get_event_store()
    store.create_session("run-one", "read", str(workspace), status="completed")
    store.append_event(
        "run-one",
        "tool_call_finished",
        payload={"tool": "read_file", "input": {"path": "README.md"}, "output": "ok", "ok": True},
        workspace_dir=str(workspace),
    )
    store.create_session("run-two", "hello", str(workspace), status="completed")

    result = build_workspace_eval_metrics(str(workspace))

    assert result["total_runs"] == 2
    assert result["completed_runs"] == 2
    assert result["metrics"]["tool_execution_rate"]["applicable_runs"] == 1
    assert result["metrics"]["tool_execution_rate"]["average"] == 1.0


def test_runtime_eval_metrics_api_is_workspace_scoped(tmp_path):
    from fastapi.testclient import TestClient

    from src.api import legacy_runtime as api_server
    from src.api.services.event_store import get_event_store

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original_workspace = api_server._get_workspace()
    try:
        api_server._set_active_workspace(str(workspace))
        store = get_event_store()
        store.create_session("api-metrics-run", "hello", str(workspace), status="completed")
        client = TestClient(api_server.app)

        run_response = client.get("/api/evals/runtime/runs/api-metrics-run/metrics")
        summary_response = client.get("/api/evals/runtime/summary")
        missing_response = client.get("/api/evals/runtime/runs/missing-run/metrics")

        assert run_response.status_code == 200
        assert run_response.json()["thread_id"] == "api-metrics-run"
        assert summary_response.status_code == 200
        assert summary_response.json()["total_runs"] == 1
        assert missing_response.status_code == 404
    finally:
        api_server._set_active_workspace(original_workspace)
