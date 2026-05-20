"""Delivery API tests — contract, finalize, regenerate endpoints."""

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.services.event_store import EventStore


class TestDeliveryAPI:
    """Test delivery endpoints with real data in a temp workspace."""

    def test_get_delivery_nonexistent_run_404(self):
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        thread_id = f"nonexistent_{uuid.uuid4().hex[:8]}"
        resp = client.get(f"/api/runs/{thread_id}/delivery")
        assert resp.status_code == 404
        data = resp.json()
        if "error" in data:
            assert "request_id" in data["error"]

    def test_get_delivery_for_run_without_data(self, tmp_path):
        """Delivery can be built on-the-fly even without prior finalize."""
        from api_server import app

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = "run_del_test_no_data"
        store.create_session(
            thread_id=thread_id,
            prompt="测试无数据 delivery",
            workspace_dir=str(ws),
            status="completed",
        )

        import src.infra.config as cfg
        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            resp = client.get(f"/api/runs/{thread_id}/delivery")
            assert resp.status_code == 200
            data = resp.json()
            assert data["thread_id"] == thread_id
            assert data["status"] in ("ready", "failed", "blocked", "draft")
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_get_delivery_uses_indexed_run_workspace_after_switch(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        run_ws = tmp_path / "run_workspace"
        other_ws = tmp_path / "other_workspace"
        run_ws.mkdir(parents=True)
        other_ws.mkdir(parents=True)

        store = EventStore()
        thread_id = f"run_cross_ws_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="cross workspace delivery",
            workspace_dir=str(run_ws),
            status="completed",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(other_ws)
            client = TestClient(app)
            resp = client.get(f"/api/runs/{thread_id}/delivery")
            assert resp.status_code == 200
            data = resp.json()
            assert data["thread_id"] == thread_id
            assert data["workspace_dir"] == str(run_ws.resolve())
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_inline_routes_resolve_indexed_run_workspace_after_switch(self, tmp_path):
        import src.infra.config as cfg
        import api_server

        run_ws = tmp_path / "run_workspace"
        other_ws = tmp_path / "other_workspace"
        run_ws.mkdir(parents=True)
        other_ws.mkdir(parents=True)

        store = EventStore()
        thread_id = f"run_inline_cross_ws_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="cross workspace inline helper",
            workspace_dir=str(run_ws),
            status="completed",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(other_ws)
            assert api_server._workspace_for_thread(thread_id) == str(run_ws.resolve())
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_finalize_nonexistent_run(self):
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        thread_id = f"noexist_{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/api/runs/{thread_id}/delivery/finalize",
            json={"force": False},
        )
        assert resp.status_code == 404

    def test_finalize_with_force_creates_delivery(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = "run_force_test"
        store.create_session(
            thread_id=thread_id,
            prompt="测试 force finalize",
            workspace_dir=str(ws),
            status="running",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            resp = client.post(
                f"/api/runs/{thread_id}/delivery/finalize",
                json={"force": True},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["thread_id"] == thread_id
            assert data["status"] == "draft"  # running → DRAFT
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_finalize_no_force_on_non_terminal(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = "run_non_terminal"
        store.create_session(
            thread_id=thread_id,
            prompt="non-terminal run",
            workspace_dir=str(ws),
            status="running",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                f"/api/runs/{thread_id}/delivery/finalize",
                json={"force": False},
            )
            assert resp.status_code == 404
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_regenerate_creates_delivery_files(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = "run_regen_files"
        store.create_session(
            thread_id=thread_id,
            prompt="测试 regenerate",
            workspace_dir=str(ws),
            status="completed",
        )
        store.update_session(
            thread_id,
            str(ws),
            execution_plan={
                "stages": [
                    {"id": "s1", "title": "plan", "owner": "planner", "status": "completed"},
                ]
            },
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            resp = client.post(
                f"/api/runs/{thread_id}/delivery/regenerate",
                json={"include_markdown": True},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["thread_id"] == thread_id
            assert len(data["plan"]) == 1
            assert data["plan"][0]["id"] == "s1"

            run_dir = ws / ".nanocursor" / "runs" / thread_id
            assert (run_dir / "delivery.json").exists()
            assert (run_dir / "delivery.md").exists()

            md = (run_dir / "delivery.md").read_text(encoding="utf-8")
            assert "测试 regenerate" in md
            assert "plan" in md
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_delivery_error_response_format(self):
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        thread_id = f"bad_run_{uuid.uuid4().hex[:8]}"
        resp = client.get(f"/api/runs/{thread_id}/delivery")
        assert resp.status_code == 404
        data = resp.json()
        if "error" in data:
            assert "code" in data["error"]
            assert "message" in data["error"]
            assert "request_id" in data["error"]
        assert "x-request-id" in resp.headers

    def test_finalize_completed_run(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = "run_completed"
        store.create_session(
            thread_id=thread_id,
            prompt="完成的任务",
            workspace_dir=str(ws),
            status="completed",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            resp = client.post(
                f"/api/runs/{thread_id}/delivery/finalize",
                json={"force": False},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"  # completed → READY
            assert data["objective"] == "完成的任务"
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_finalize_failed_run(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = "run_failed_test"
        store.create_session(
            thread_id=thread_id,
            prompt="失败的任务",
            workspace_dir=str(ws),
            status="failed",
        )
        store.update_session(thread_id, str(ws), error="something went wrong")

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            resp = client.post(
                f"/api/runs/{thread_id}/delivery/finalize",
                json={"force": False},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "failed"
            assert len(data["next_actions"]) > 0
        finally:
            cfg.WORKSPACE_DIR = old_ws

    def test_regenerate_without_markdown(self, tmp_path):
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        store = EventStore()
        thread_id = "run_md_skip"
        store.create_session(
            thread_id=thread_id,
            prompt="markdown skip test",
            workspace_dir=str(ws),
            status="completed",
        )

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            resp = client.post(
                f"/api/runs/{thread_id}/delivery/regenerate",
                json={"include_markdown": False},
            )
            assert resp.status_code == 200

            run_dir = ws / ".nanocursor" / "runs" / thread_id
            assert (run_dir / "delivery.json").exists()
            assert not (run_dir / "delivery.md").exists()
        finally:
            cfg.WORKSPACE_DIR = old_ws
