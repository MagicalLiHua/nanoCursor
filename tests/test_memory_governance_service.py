from __future__ import annotations


def _workspace(tmp_path, name="workspace"):
    workspace = tmp_path / name
    workspace.mkdir()
    return workspace


def test_memory_records_are_workspace_scoped(tmp_path):
    from src.api.services.memory_governance_service import create_memory_record, list_memory_records

    first = _workspace(tmp_path, "first")
    second = _workspace(tmp_path, "second")
    created = create_memory_record(
        str(first),
        scope="workspace",
        kind="workflow_note",
        content="Use pytest -q for the backend suite.",
        source="user",
    )

    assert [item["id"] for item in list_memory_records(str(first))] == [created["id"]]
    assert list_memory_records(str(second)) == []


def test_file_memory_becomes_stale_when_file_changes(tmp_path):
    from src.api.services.memory_governance_service import (
        create_memory_record,
        get_memory_record,
        refresh_memory_freshness,
    )

    workspace = _workspace(tmp_path)
    target = workspace / "app.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    fact = create_memory_record(
        str(workspace),
        scope="file",
        file_path="app.py",
        kind="project_fact",
        content="app.py defines VALUE = 1.",
        source="user",
        confidence=0.95,
    )
    failure = create_memory_record(
        str(workspace),
        scope="file",
        file_path="app.py",
        kind="failure_pattern",
        content="Changing VALUE without tests previously caused a regression.",
        source="failure_recovery",
        confidence=0.8,
    )

    target.write_text("VALUE = 2\n", encoding="utf-8")
    refreshed = refresh_memory_freshness(str(workspace))

    assert set(refreshed["stale_ids"]) == {fact["id"], failure["id"]}
    assert get_memory_record(str(workspace), fact["id"])["status"] == "stale"
    failure_record = get_memory_record(str(workspace), failure["id"])
    assert failure_record["status"] == "active"
    assert failure_record["freshness"] == "stale"
    assert failure_record["confidence"] <= 0.45


def test_automatic_memory_rejects_secrets_and_unverified_facts(tmp_path):
    import pytest

    from src.api.services.memory_governance_service import create_memory_record

    workspace = _workspace(tmp_path)
    with pytest.raises(ValueError, match="automatic memory rejected"):
        create_memory_record(
            str(workspace),
            scope="workspace",
            kind="workflow_note",
            content="api_key = sk-super-secret-value",
            source="system_summary",
            automatic=True,
        )

    with pytest.raises(ValueError, match="require evidence_refs"):
        create_memory_record(
            str(workspace),
            scope="workspace",
            kind="project_fact",
            content="The production entrypoint is server.py.",
            source="system_summary",
            automatic=True,
        )


def test_disabled_and_deleted_memory_are_user_controllable(tmp_path):
    from src.api.services.memory_governance_service import (
        create_memory_record,
        delete_memory_record,
        get_memory_record,
        list_memory_records,
        update_memory_record,
    )

    workspace = _workspace(tmp_path)
    created = create_memory_record(
        str(workspace),
        scope="workspace",
        kind="project_rule",
        content="Never modify generated files.",
        source="user",
    )

    disabled = update_memory_record(str(workspace), created["id"], status="disabled")
    assert disabled["status"] == "disabled"
    assert delete_memory_record(str(workspace), created["id"]) is True
    assert get_memory_record(str(workspace), created["id"])["status"] == "deleted"
    assert list_memory_records(str(workspace)) == []


def test_run_memory_extraction_is_evidence_backed_and_idempotent(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.memory_governance_service import extract_run_memory, list_memory_records

    workspace = _workspace(tmp_path)
    thread_id = "completed-run-memory"
    store = get_event_store()
    store.create_session(thread_id, "修复 app.py", str(workspace), status="completed")
    store.update_session(
        thread_id,
        str(workspace),
        execution_plan={"strategy": "bug_fix"},
        execution_summary="修复 app.py 并通过 pytest。",
    )

    first = extract_run_memory(str(workspace), thread_id)
    second = extract_run_memory(str(workspace), thread_id)

    assert first["created"] is True
    assert first["memory"]["source"] == "run_evidence"
    assert second["created"] is False
    assert len(list_memory_records(str(workspace), scope="run", run_id=thread_id)) == 1


def test_failure_learner_writes_governed_memory_and_skips_secret_output(tmp_path):
    from src.agent.engine import bind_runtime_context
    from src.agent.learner import FailureLearner
    from src.api.services.memory_governance_service import list_memory_records

    workspace = _workspace(tmp_path)
    learner = FailureLearner()
    with bind_runtime_context({"workspace_dir": str(workspace), "thread_id": "run-failure"}):
        learner.on_tool_failure("bash", {"command": "pytest -q"}, "AssertionError: expected 1")
        learner.on_tool_failure("bash", {"command": "deploy"}, "Error: api_key = sk-super-secret-value")

    records = list_memory_records(str(workspace))
    assert len(records) == 1
    assert records[0]["kind"] == "failure_pattern"
    assert records[0]["source"] == "failure_recovery"
    assert records[0]["evidence_refs"] == ["run:run-failure"]
    assert learner.build_learning_context() == ""


def test_experience_learner_skips_unsafe_episode_without_breaking_run(tmp_path):
    from src.agent.engine import bind_runtime_context
    from src.agent.learner import ExperienceLearner
    from src.api.services.memory_governance_service import list_memory_records

    workspace = _workspace(tmp_path)
    learner = ExperienceLearner()
    with bind_runtime_context({"workspace_dir": str(workspace), "thread_id": "run-success"}):
        learner.start_episode("update app")
        learner.record_call("write_file", {"path": "app.py"}, "updated")
        learner.record_call("bash", {"command": "pytest -q"}, "1 passed")
        memory_id = learner.complete_episode(summary="api_key = sk-super-secret-value")

    assert memory_id is None
    assert list_memory_records(str(workspace)) == []


def test_experience_recall_preserves_episode_tags(tmp_path):
    from src.agent.engine import bind_runtime_context
    from src.agent.learner import ExperienceLearner

    workspace = _workspace(tmp_path)
    learner = ExperienceLearner()
    with bind_runtime_context({"workspace_dir": str(workspace), "thread_id": "run-success"}):
        learner.start_episode("update parser")
        learner.record_call("write_file", {"path": "parser.py"}, "updated parser")
        learner.record_call("bash", {"command": "pytest parser"}, "1 passed")
        assert learner.complete_episode(summary="Parser regression tests passed.") is not None
        recalled = learner.retrieve_relevant("update parser and run pytest")

    assert len(recalled) == 1
    assert recalled[0]["tags"][:2] == ["episode", "success"]
