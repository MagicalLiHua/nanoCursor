from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import legacy_runtime as api_server


def test_governed_memory_crud_and_preview_api(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(api_server.app)

    created = client.post(
        "/api/memory",
        json={
            "workspace_dir": str(workspace),
            "scope": "workspace",
            "kind": "workflow_note",
            "content": "Use pytest -q for backend verification.",
            "importance": 8,
        },
    )
    assert created.status_code == 200
    memory_id = created.json()["memory"]["id"]

    listed = client.get("/api/memory", params={"workspace_dir": str(workspace)})
    assert listed.status_code == 200
    assert listed.json()["memories"][0]["id"] == memory_id

    preview = client.post(
        "/api/context/memory/preview",
        json={
            "workspace_dir": str(workspace),
            "prompt": "How should I run backend verification with pytest?",
            "budget": 500,
        },
    )
    assert preview.status_code == 200
    assert any(item["id"] == memory_id for item in preview.json()["selected"])

    disabled = client.patch(
        f"/api/memory/{memory_id}",
        json={"workspace_dir": str(workspace), "status": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["memory"]["status"] == "disabled"

    deleted = client.delete(f"/api/memory/{memory_id}", params={"workspace_dir": str(workspace)})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_direct_answer_run_does_not_extract_long_term_memory(tmp_path):
    from src.api.services.event_store import get_event_store

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "direct-answer-memory"
    store = get_event_store()
    store.create_session(thread_id, "哈喽", str(workspace), status="completed")
    store.update_session(
        thread_id,
        str(workspace),
        execution_plan={"strategy": "lead_direct_reply"},
        execution_summary="哈喽！有什么可以帮你？",
    )
    client = TestClient(api_server.app)

    response = client.post(
        f"/api/runs/{thread_id}/memory/extract",
        params={"workspace_dir": str(workspace)},
    )

    assert response.status_code == 200
    assert response.json()["created"] is False
    assert "lead_direct_reply" in response.json()["reason"]


def test_legacy_memory_api_delegates_to_governed_memory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(api_server.app)

    created = client.post(
        "/api/memories",
        params={
            "workspace_dir": str(workspace),
            "content": "The project uses pytest for backend verification.",
            "category": "project",
            "importance": 8,
            "tags": '["pytest", "verification"]',
        },
    )
    assert created.status_code == 200
    memory = created.json()["memory"]
    memory_id = memory["id"]
    assert memory["category"] == "project"
    assert memory["scope"] == "workspace"
    assert memory["kind"] == "project_fact"

    governed = client.get("/api/memory", params={"workspace_dir": str(workspace)})
    assert governed.status_code == 200
    assert [item["id"] for item in governed.json()["memories"]] == [memory_id]

    listed = client.get(
        "/api/memories",
        params={"workspace_dir": str(workspace), "category": "project"},
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["memories"]] == [memory_id]

    searched = client.get(
        "/api/memories/search",
        params={"workspace_dir": str(workspace), "q": "PYTEST"},
    )
    assert searched.status_code == 200
    assert [item["id"] for item in searched.json()["memories"]] == [memory_id]

    updated = client.patch(
        f"/api/memories/{memory_id}",
        params={
            "workspace_dir": str(workspace),
            "content": "Run pytest -q before delivery.",
            "importance": 9,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["memory"]["content"] == "Run pytest -q before delivery."
    assert updated.json()["memory"]["importance"] == 9

    deleted = client.delete(
        f"/api/memories/{memory_id}",
        params={"workspace_dir": str(workspace)},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    active = client.get("/api/memories", params={"workspace_dir": str(workspace)})
    assert active.status_code == 200
    assert active.json()["memories"] == []
