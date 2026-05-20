"""Change set API tests."""

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.services.event_store import EventStore


class TestChangeAPI:
    def test_get_changes_nonexistent_run_404(self):
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        thread_id = f"noexist_{uuid.uuid4().hex[:8]}"
        resp = client.get(f"/api/runs/{thread_id}/changes")
        assert resp.status_code == 404
        if "error" in resp.json():
            assert "request_id" in resp.json()["error"]

    def test_collect_and_get_changes(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        (ws / "file.txt").write_text("hello")

        store = EventStore()
        thread_id = f"run_cs_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="test change set",
            workspace_dir=str(ws),
            status="running",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            # Collect
            resp = client.post(
                f"/api/runs/{thread_id}/changes/collect",
                json={"include_untracked": True},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["thread_id"] == thread_id
            assert data["status"] == "collected"

            # Get
            resp = client.get(f"/api/runs/{thread_id}/changes")
            assert resp.status_code == 200
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_review_after_collect(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = f"run_rev_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="review test",
            workspace_dir=str(ws),
            status="running",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            client.post(
                f"/api/runs/{thread_id}/changes/collect",
                json={"include_untracked": True},
            )
            resp = client.post(
                f"/api/runs/{thread_id}/changes/review",
                json={"mode": "rule_first"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "reviewed"
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_approve_changes(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = f"run_apr_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="approve test",
            workspace_dir=str(ws),
            status="running",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            client.post(
                f"/api/runs/{thread_id}/changes/collect",
                json={"include_untracked": True},
            )
            resp = client.post(
                f"/api/runs/{thread_id}/changes/approve",
                json={"approved": True, "comment": "可以交付"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "approved"
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_reject_changes(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = f"run_rej_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="reject test",
            workspace_dir=str(ws),
            status="running",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            client.post(
                f"/api/runs/{thread_id}/changes/collect",
                json={"include_untracked": True},
            )
            resp = client.post(
                f"/api/runs/{thread_id}/changes/approve",
                json={"approved": False, "comment": "需要更多测试"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "rejected"
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_approve_without_collect_404(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = f"run_noap_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="no collect",
            workspace_dir=str(ws),
            status="running",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                f"/api/runs/{thread_id}/changes/approve",
                json={"approved": True},
            )
            assert resp.status_code == 404
        finally:
            cfg.WORKSPACE_DIR = old_ws
