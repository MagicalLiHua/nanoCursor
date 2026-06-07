import asyncio
import json

import pytest
from fastapi import HTTPException

import src.infra.config as config_module
from src.api.routes.config import get_snapshot


def test_get_snapshot_rejects_path_traversal(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    snapshots = workspace / ".snapshots"
    outside = tmp_path / "outside"
    snapshots.mkdir(parents=True)
    outside.mkdir()
    (outside / "metadata.json").write_text(
        json.dumps({"timestamp": "x", "reason": "leak", "active_files": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(workspace))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_snapshot("../../outside"))

    assert exc.value.status_code == 404


def test_get_snapshot_allows_snapshot_inside_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    snapshot = workspace / ".snapshots" / "snap-1"
    snapshot.mkdir(parents=True)
    (snapshot / "metadata.json").write_text(
        json.dumps({"timestamp": "now", "reason": "ok", "active_files": ["a.py"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(workspace))

    result = asyncio.run(get_snapshot("snap-1"))

    assert result.metadata.reason == "ok"
    assert result.metadata.active_files == ["a.py"]
