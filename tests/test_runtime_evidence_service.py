def test_runtime_delivery_evidence_requires_real_changes(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.runtime_evidence_service import collect_runtime_delivery_evidence

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "evidence-no-change"
    get_event_store().create_session(thread_id, "edit README", str(workspace), status="running")

    evidence = collect_runtime_delivery_evidence(
        thread_id,
        str(workspace),
        tool_calls=[{"tool": "write_file", "input": {"path": "README.md"}, "ok": True}],
    )

    assert evidence.ready is False
    assert evidence.has_changes is False
    assert "未检测到真实文件变更" in evidence.reason


def test_runtime_delivery_evidence_accepts_file_change_and_diff_event(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.runtime_evidence_service import collect_runtime_delivery_evidence

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "evidence-change"
    store = get_event_store()
    store.create_session(thread_id, "edit README", str(workspace), status="running")
    store.append_event(
        thread_id,
        "file_changed",
        title="文件变更：README.md",
        content="updated",
        agent="coder",
        payload={"path": "README.md", "change_type": "modified", "tool": "write_file"},
        workspace_dir=str(workspace),
    )
    store.append_event(
        thread_id,
        "diff_updated",
        title="Diff 已更新",
        content="1 个文件发生变化",
        agent="coder",
        payload={"changed_files": [{"path": "README.md", "status": "event"}], "source": "events"},
        workspace_dir=str(workspace),
    )

    evidence = collect_runtime_delivery_evidence(
        thread_id,
        str(workspace),
        tool_calls=[{"tool": "write_file", "input": {"path": "README.md"}, "ok": True}],
    )

    assert evidence.ready is True
    assert evidence.has_changes is True
    assert evidence.has_verification is True
    assert evidence.changed_files[0]["path"] == "README.md"


def test_runtime_delivery_evidence_does_not_borrow_existing_workspace_changes(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.runtime_evidence_service import collect_runtime_delivery_evidence

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "evidence-existing-change"
    store = get_event_store()
    store.create_session(thread_id, "edit README", str(workspace), status="running")
    store.append_event(
        thread_id,
        "file_changed",
        title="已有文件变更",
        content="old change",
        agent="coder",
        payload={"path": "README.md", "change_type": "modified", "tool": "write_file"},
        workspace_dir=str(workspace),
    )
    store.append_event(
        thread_id,
        "diff_updated",
        title="已有 Diff",
        content="old diff",
        agent="coder",
        payload={"changed_files": [{"path": "README.md", "status": "event"}], "source": "events"},
        workspace_dir=str(workspace),
    )

    evidence = collect_runtime_delivery_evidence(thread_id, str(workspace), tool_calls=[])

    assert evidence.has_changes is True
    assert evidence.has_write_action is False
    assert evidence.ready is False
    assert "本轮成功写入工具调用" in evidence.reason
