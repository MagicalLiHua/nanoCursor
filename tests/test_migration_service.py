"""Workspace migration service tests."""

import json

from fastapi.testclient import TestClient

from src.api.services.migration_service import (
    inspect_workspace_migrations,
    migrate_workspace,
)


def test_migration_inspects_missing_manifest_and_index(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    report = inspect_workspace_migrations(str(workspace))

    assert report["ok"] is False
    assert "ensure_workspace_manifest" in report["actions"]
    assert "rebuild_run_index" in report["actions"]


def test_migration_creates_manifest_and_run_index(tmp_path):
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".nanocursor" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "session.json").write_text(
        json.dumps({
            "thread_id": "run-1",
            "prompt": "legacy prompt",
            "status": "completed",
            "created_at": 1,
            "updated_at": 2,
            "workspace_dir": str(workspace),
        }),
        encoding="utf-8",
    )

    result = migrate_workspace(str(workspace))

    assert result["migrated"] is True
    assert result["after"]["ok"] is True
    manifest = json.loads((workspace / ".nanocursor" / "workspace.json").read_text(encoding="utf-8"))
    index = json.loads((workspace / ".nanocursor" / "runs" / "index.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert index["runs"][0]["thread_id"] == "run-1"


def test_migration_backs_up_existing_metadata_before_overwrite(tmp_path):
    workspace = tmp_path / "workspace"
    nc_dir = workspace / ".nanocursor"
    runs_dir = nc_dir / "runs"
    runs_dir.mkdir(parents=True)
    (nc_dir / "workspace.json").write_text(
        json.dumps({"workspace_id": "legacy", "name": "old", "path": str(workspace)}),
        encoding="utf-8",
    )
    (runs_dir / "index.json").write_text("{bad json", encoding="utf-8")

    result = migrate_workspace(str(workspace))

    assert result["backup"]["files"]
    backup_paths = [item["backup"] for item in result["backup"]["files"]]
    assert any(path.endswith("workspace.json") for path in backup_paths)
    assert any(path.endswith("index.json") for path in backup_paths)


def test_migration_dry_run_does_not_write_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = migrate_workspace(str(workspace), dry_run=True)

    assert result["dry_run"] is True
    assert result["migrated"] is False
    assert not (workspace / ".nanocursor" / "workspace.json").exists()


def test_workspace_migration_api(tmp_path):
    from src.api import legacy_runtime as api_server

    original_workspace = api_server._get_workspace()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        api_server._set_active_workspace(str(workspace))
        client = TestClient(api_server.app)

        inspect_resp = client.get("/api/workspace/migration")
        assert inspect_resp.status_code == 200
        assert "ensure_workspace_manifest" in inspect_resp.json()["actions"]

        migrate_resp = client.post("/api/workspace/migrate")
        assert migrate_resp.status_code == 200
        assert migrate_resp.json()["after"]["ok"] is True
    finally:
        api_server._set_active_workspace(original_workspace)
