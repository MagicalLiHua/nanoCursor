"""Run ledger — persistence and query tests."""

import json
import tempfile
import uuid
from pathlib import Path

from src.runtime.run_ledger import (
    RunLedger,
    RunLedgerRepository,
    StepRecord,
    ToolCallRecord,
    get_ledger_repo,
)
from src.api.services.run_ledger_service import (
    get_run_ledger,
    get_run_steps,
    get_run_tools,
    record_steps,
    record_tool_call_finish,
    record_tool_call_start,
    sync_steps_from_lifecycle,
)


class TestToolCallRecord:
    def test_default_values(self):
        tc = ToolCallRecord(
            call_id="call_001",
            thread_id="run_1",
            tool_name="write_file",
        )
        assert tc.status == "started"
        assert tc.step_id == ""
        assert tc.output_tail == ""

    def test_full_values(self):
        tc = ToolCallRecord(
            call_id="call_002",
            thread_id="run_2",
            step_id="step_1",
            tool_name="bash",
            input_json='{"cmd":"pytest"}',
            output_tail="12 passed",
            status="completed",
            started_at=1000.0,
            completed_at=1005.0,
        )
        d = tc.model_dump()
        assert d["step_id"] == "step_1"
        assert d["status"] == "completed"


class TestStepRecord:
    def test_default_values(self):
        s = StepRecord(step_id="step_1", thread_id="run_1", title="Plan")
        assert s.status == "pending"
        assert s.owner == ""

    def test_full_values(self):
        s = StepRecord(
            step_id="step_2",
            thread_id="run_2",
            title="Implement",
            owner="coder",
            status="completed",
            started_at=1000.0,
            completed_at=1020.0,
            error="",
        )
        d = s.model_dump()
        assert d["owner"] == "coder"
        assert d["status"] == "completed"


class TestRunLedgerRepository:
    def test_append_and_read_tool_calls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            repo = RunLedgerRepository()
            repo.append_tool_call(
                "run_tc",
                ToolCallRecord(call_id="c1", thread_id="run_tc", tool_name="write_file", status="started"),
                str(ws),
            )
            repo.append_tool_call(
                "run_tc",
                ToolCallRecord(call_id="c2", thread_id="run_tc", tool_name="bash", status="completed"),
                str(ws),
            )
            calls = repo.get_tool_calls("run_tc", str(ws))
            assert len(calls) == 2
            assert calls[0].tool_name == "write_file"
            assert calls[1].tool_name == "bash"

    def test_write_and_read_steps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            repo = RunLedgerRepository()
            steps = [
                StepRecord(step_id="s1", thread_id="run_st", title="Plan", status="completed"),
                StepRecord(step_id="s2", thread_id="run_st", title="Code", status="running"),
            ]
            repo.write_steps("run_st", steps, str(ws))
            loaded = repo.get_steps("run_st", str(ws))
            assert len(loaded) == 2
            assert loaded[0].title == "Plan"

    def test_get_steps_missing_returns_empty(self):
        repo = RunLedgerRepository()
        steps = repo.get_steps("nonexistent")
        assert steps == []

    def test_get_tool_calls_missing_returns_empty(self):
        repo = RunLedgerRepository()
        calls = repo.get_tool_calls("nonexistent")
        assert calls == []

    def test_build_ledger_no_session_returns_none(self):
        repo = RunLedgerRepository()
        ledger = repo.build_ledger("nonexistent_run_ledger")
        assert ledger is None

    def test_build_ledger_with_session(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        rd = ws / ".nanocursor" / "runs" / "run_led"
        rd.mkdir(parents=True)
        (rd / "session.json").write_text(json.dumps({
            "thread_id": "run_led",
            "workspace_dir": str(ws),
            "status": "completed",
            "mode": "agenthub_delivery",
            "created_at": 1000.0,
            "updated_at": 2000.0,
        }))

        repo = RunLedgerRepository()
        ledger = repo.build_ledger("run_led", str(ws))
        assert ledger is not None
        assert ledger.thread_id == "run_led"
        assert ledger.status == "completed"
        assert ledger.mode == "agenthub_delivery"

    def test_build_ledger_includes_artifacts(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        rd = ws / ".nanocursor" / "runs" / "run_art"
        rd.mkdir(parents=True)
        (rd / "session.json").write_text(json.dumps({
            "thread_id": "run_art",
            "workspace_dir": str(ws),
            "status": "completed",
            "mode": "agenthub_delivery",
            "created_at": 1000.0,
            "updated_at": 1000.0,
        }))
        (rd / "delivery.json").write_text(json.dumps({"status": "ready"}))
        (rd / "changes.json").write_text(json.dumps({"status": "approved"}))
        approvals_dir = rd / "approvals"
        approvals_dir.mkdir()
        (approvals_dir / "a1.json").write_text("{}")
        (approvals_dir / "a2.json").write_text("{}")

        repo = RunLedgerRepository()
        ledger = repo.build_ledger("run_art", str(ws))
        assert ledger.delivery_status == "ready"
        assert ledger.changes_status == "approved"
        assert ledger.approval_count == 2


class TestRunLedgerService:
    def test_record_tool_call_start_and_finish(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        rec = record_tool_call_start(
            "run_svc", "bash", {"cmd": "ls"}, step_id="step_1", workspace_dir=str(ws),
        )
        assert rec.status == "started"
        assert rec.tool_name == "bash"

        record_tool_call_finish(rec.call_id, "run_svc", output="ok", ok=True, workspace_dir=str(ws))

        tools = get_run_tools("run_svc", str(ws))
        assert len(tools) == 1
        assert tools[0].status == "completed"
        assert tools[0].output_tail == "ok"

    def test_record_steps_and_read_back(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        stages = [
            {"id": "s1", "title": "Plan", "owner": "planner", "status": "completed", "started_at": 1.0, "completed_at": 2.0},
            {"id": "s2", "title": "Code", "owner": "coder", "status": "running", "started_at": 2.0, "completed_at": 0.0},
        ]
        record_steps("run_steps", stages, str(ws))
        steps = get_run_steps("run_steps", str(ws))
        assert len(steps) == 2
        assert steps[0].title == "Plan"
        assert steps[0].status == "completed"

    def test_sync_steps_from_lifecycle(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        rd = ws / ".nanocursor" / "runs" / "run_life"
        rd.mkdir(parents=True)
        (rd / "session.json").write_text(json.dumps({
            "thread_id": "run_life",
            "workspace_dir": str(ws),
            "status": "running",
            "created_at": 1000.0,
        }))

        metadata = {
            "lifecycle": {
                "current_stage_id": "s2",
                "stages": [
                    {"id": "s1", "title": "Analyze", "owner": "planner", "status": "completed", "started_at": 1.0, "completed_at": 2.0},
                    {"id": "s2", "title": "Implement", "owner": "coder", "status": "running", "started_at": 2.0, "completed_at": 0.0},
                ],
            }
        }
        sync_steps_from_lifecycle("run_life", metadata, str(ws))
        steps = get_run_steps("run_life", str(ws))
        assert len(steps) == 2
        assert steps[1].title == "Implement"

    def test_get_run_ledger_integrated(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        rd = ws / ".nanocursor" / "runs" / "run_int"
        rd.mkdir(parents=True)
        (rd / "session.json").write_text(json.dumps({
            "thread_id": "run_int",
            "workspace_dir": str(ws),
            "status": "completed",
            "mode": "agenthub_delivery",
            "created_at": 1000.0,
            "updated_at": 2000.0,
        }))
        (rd / "delivery.json").write_text(json.dumps({"status": "ready"}))

        repo = RunLedgerRepository()
        repo.write_steps("run_int", [
            StepRecord(step_id="s1", thread_id="run_int", title="Done", status="completed"),
        ], str(ws))
        repo.append_tool_call("run_int",
            ToolCallRecord(call_id="c1", thread_id="run_int", tool_name="write_file", status="completed"),
            str(ws))

        ledger = get_run_ledger("run_int", str(ws))
        assert ledger is not None
        assert len(ledger.steps) == 1
        assert len(ledger.tool_calls) == 1
        assert ledger.delivery_status == "ready"

    def test_get_run_ledger_missing(self):
        ledger = get_run_ledger("no_such_ledger")
        assert ledger is None

    def test_tool_call_deduplication(self, tmp_path):
        """Multiple writes with same call_id should merge into one record."""
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        rec = record_tool_call_start(
            "run_dedup", "write_file", {"path": "a.py"}, workspace_dir=str(ws),
        )
        # Simulate a finish write
        record_tool_call_finish(rec.call_id, "run_dedup", output="done", ok=True, workspace_dir=str(ws))

        tools = get_run_tools("run_dedup", str(ws))
        assert len(tools) == 1
        assert tools[0].tool_name == "write_file"
        assert tools[0].status == "completed"
        assert tools[0].output_tail == "done"


class TestRunLedgerAPI:
    def test_get_ledger_nonexistent_404(self):
        from fastapi.testclient import TestClient
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        thread_id = f"no_ledger_{uuid.uuid4().hex[:8]}"
        resp = client.get(f"/api/runs/{thread_id}/ledger")
        assert resp.status_code == 404

    def test_get_steps_nonexistent(self):
        from fastapi.testclient import TestClient
        from api_server import app
        client = TestClient(app)
        thread_id = f"no_steps_{uuid.uuid4().hex[:8]}"
        resp = client.get(f"/api/runs/{thread_id}/steps")
        assert resp.status_code == 200
        data = resp.json()
        assert data["steps"] == []
        assert data["total"] == 0

    def test_get_tools_nonexistent(self):
        from fastapi.testclient import TestClient
        from api_server import app
        client = TestClient(app)
        thread_id = f"no_tools_{uuid.uuid4().hex[:8]}"
        resp = client.get(f"/api/runs/{thread_id}/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tools"] == []
        assert data["total"] == 0

    def test_full_ledger_workflow(self, tmp_path):
        """End-to-end: create session, record tools+steps, query ledger."""
        from fastapi.testclient import TestClient
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        thread_id = f"e2e_{uuid.uuid4().hex[:8]}"

        # Create session
        from src.api.services.event_store import EventStore
        store = EventStore()
        store.create_session(thread_id=thread_id, prompt="E2E test", workspace_dir=str(ws), status="running")

        old_ws = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)

            # Record a tool call via the ledger service
            record_tool_call_start(thread_id, "write_file", {"path": "a.py"}, workspace_dir=str(ws))

            # Record steps
            record_steps(thread_id, [
                {"id": "s1", "title": "Plan", "owner": "planner", "status": "completed"},
            ], str(ws))

            # Query ledger
            resp = client.get(f"/api/runs/{thread_id}/ledger")
            assert resp.status_code == 200
            data = resp.json()
            assert data["thread_id"] == thread_id

            # Query steps
            resp2 = client.get(f"/api/runs/{thread_id}/steps")
            assert resp2.status_code == 200
            assert resp2.json()["total"] == 1

            # Query tools
            resp3 = client.get(f"/api/runs/{thread_id}/tools")
            assert resp3.status_code == 200
            assert resp3.json()["total"] == 1
        finally:
            cfg.WORKSPACE_DIR = old_ws
