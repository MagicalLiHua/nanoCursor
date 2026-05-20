"""Delivery contract model and markdown rendering tests."""

import json
import os
import tempfile
from pathlib import Path

from src.runtime.delivery_contract import (
    DeliveryContract,
    DeliveryFileChange,
    DeliveryStatus,
    DeliveryVerification,
)
from src.api.services.delivery_service import (
    _classify_risk,
    build_delivery_contract,
    finalize_delivery,
    load_delivery_contract,
    regenerate_delivery,
    render_delivery_markdown,
    save_delivery_contract,
    save_delivery_markdown,
)


class TestDeliveryContractModel:
    def test_default_values(self):
        c = DeliveryContract(
            thread_id="run_001",
            workspace_dir="/tmp/test",
            status=DeliveryStatus.READY,
        )
        assert c.objective == ""
        assert c.changed_files == []
        assert c.verifications == []
        assert c.risks == []
        assert c.next_actions == []

    def test_status_enum_values(self):
        assert DeliveryStatus.DRAFT.value == "draft"
        assert DeliveryStatus.READY.value == "ready"
        assert DeliveryStatus.BLOCKED.value == "blocked"
        assert DeliveryStatus.FAILED.value == "failed"

    def test_file_change_model(self):
        fc = DeliveryFileChange(
            path="src/main.py",
            change_type="modified",
            additions=12,
            deletions=4,
            summary="fix bug",
            risk="medium",
        )
        d = fc.model_dump()
        assert d["path"] == "src/main.py"
        assert d["additions"] == 12
        assert d["risk"] == "medium"

    def test_verification_model(self):
        v = DeliveryVerification(
            command="pytest -q",
            exit_code=0,
            status="passed",
            stdout_tail="12 passed",
            duration_ms=3400,
        )
        d = v.model_dump()
        assert d["status"] == "passed"
        assert d["exit_code"] == 0


class TestDeliveryMarkdown:
    def test_markdown_includes_objective(self):
        c = DeliveryContract(
            thread_id="run_001",
            workspace_dir="/tmp/test",
            status=DeliveryStatus.READY,
            objective="修复登录按钮",
            summary="已完成修复",
            generated_at="2026-05-18T12:00:00Z",
        )
        md = render_delivery_markdown(c)
        assert "修复登录按钮" in md
        assert "已完成修复" in md
        assert "run_001" in md

    def test_markdown_includes_changes(self):
        c = DeliveryContract(
            thread_id="run_001",
            workspace_dir="/tmp/test",
            status=DeliveryStatus.READY,
            changed_files=[
                DeliveryFileChange(path="src/Login.tsx", change_type="modified", risk="medium"),
                DeliveryFileChange(path="src/old.ts", change_type="deleted", risk="high"),
            ],
            generated_at="2026-05-18T12:00:00Z",
        )
        md = render_delivery_markdown(c)
        assert "src/Login.tsx" in md
        assert "modified" in md
        assert "src/old.ts" in md
        assert "high" in md
        assert "## Changed Files" in md

    def test_markdown_includes_verifications(self):
        c = DeliveryContract(
            thread_id="run_001",
            workspace_dir="/tmp/test",
            status=DeliveryStatus.READY,
            verifications=[
                DeliveryVerification(
                    command="npm test",
                    exit_code=0,
                    status="passed",
                    stdout_tail="12 passed",
                    duration_ms=3400,
                ),
            ],
            generated_at="2026-05-18T12:00:00Z",
        )
        md = render_delivery_markdown(c)
        assert "npm test" in md
        assert "passed" in md
        assert "3400ms" in md
        assert "## Verifications" in md

    def test_markdown_includes_risks(self):
        c = DeliveryContract(
            thread_id="run_001",
            workspace_dir="/tmp/test",
            status=DeliveryStatus.READY,
            risks=[{"description": "高风险文件变更"}],
            generated_at="2026-05-18T12:00:00Z",
        )
        md = render_delivery_markdown(c)
        assert "高风险文件变更" in md
        assert "## Risks" in md

    def test_markdown_includes_plan(self):
        c = DeliveryContract(
            thread_id="run_001",
            workspace_dir="/tmp/test",
            status=DeliveryStatus.READY,
            plan=[
                {"id": "s1", "title": "分析需求", "owner": "planner", "status": "completed"},
                {"id": "s2", "title": "实现代码", "owner": "coder", "status": "completed"},
            ],
            generated_at="2026-05-18T12:00:00Z",
        )
        md = render_delivery_markdown(c)
        assert "分析需求" in md
        assert "planner" in md
        assert "completed" in md

    def test_markdown_empty_sections_still_present(self):
        c = DeliveryContract(
            thread_id="run_001",
            workspace_dir="/tmp/test",
            status=DeliveryStatus.DRAFT,
            generated_at="2026-05-18T12:00:00Z",
        )
        md = render_delivery_markdown(c)
        assert "## Changed Files" in md
        assert "## Verifications" in md
        assert "## Risks" in md
        assert "## Open Questions" in md
        assert "## Next Actions" in md


class TestRiskClassification:
    def test_deleted_file_high(self):
        assert _classify_risk("src/old.py", "deleted", 0, 0) == "high"

    def test_large_change_high(self):
        assert _classify_risk("src/big.py", "modified", 300, 250) == "high"

    def test_lockfile_high(self):
        assert _classify_risk("package-lock.json", "modified", 50, 50) == "high"

    def test_env_file_high(self):
        assert _classify_risk(".env", "modified", 3, 1) == "high"

    def test_ci_config_medium(self):
        assert _classify_risk(".github/workflows/ci.yml", "modified", 10, 5) == "medium"

    def test_test_file_low(self):
        assert _classify_risk("tests/test_auth.py", "modified", 20, 5) == "low"

    def test_normal_file_medium(self):
        assert _classify_risk("src/utils.py", "modified", 30, 10) == "medium"


class TestDeliveryPersistence:
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Use tmpdir as workspace so .nanocursor/runs is created inside it
            ws = Path(tmpdir)
            c = DeliveryContract(
                thread_id="run_test_001",
                workspace_dir=str(ws),
                status=DeliveryStatus.READY,
                objective="测试持久化",
                summary="验证读写一致性",
                generated_at="2026-05-18T12:00:00Z",
            )
            path = save_delivery_contract(c)
            assert path.exists()
            assert path.suffix == ".json"

            loaded = load_delivery_contract("run_test_001", str(ws))
            assert loaded is not None
            assert loaded.thread_id == "run_test_001"
            assert loaded.objective == "测试持久化"

    def test_load_missing_returns_none(self):
        loaded = load_delivery_contract("nonexistent_run_xyz_999")
        assert loaded is None

    def test_atomic_write_does_not_leave_temp_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            c = DeliveryContract(
                thread_id="run_test_002",
                workspace_dir=str(ws),
                status=DeliveryStatus.READY,
                generated_at="2026-05-18T12:00:00Z",
            )
            path = save_delivery_contract(c)
            parent = path.parent
            # No .tmp files left behind
            tmp_files = list(parent.glob(".*.tmp"))
            assert len(tmp_files) == 0

    def test_markdown_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            c = DeliveryContract(
                thread_id="run_test_003",
                workspace_dir=str(ws),
                status=DeliveryStatus.READY,
                objective="生成 markdown",
                summary="测试 md 持久化",
                generated_at="2026-05-18T12:00:00Z",
            )
            path = save_delivery_markdown(c)
            assert path.exists()
            assert path.suffix == ".md"
            content = path.read_text(encoding="utf-8")
            assert "生成 markdown" in content
            assert "测试 md 持久化" in content
            tmp_files = list(path.parent.glob(".*.tmp"))
            assert tmp_files == []

    def test_load_corrupt_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            rd = ws / ".nanocursor" / "runs" / "run_bad"
            rd.mkdir(parents=True)
            (rd / "delivery.json").write_text("not valid json {{{", encoding="utf-8")
            loaded = load_delivery_contract("run_bad", str(ws))
            assert loaded is None


class TestDeliveryBuildSemantics:
    def test_completed_run_with_failed_verification_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            thread_id = "run_failed_verification"
            from src.api.services.event_store import EventStore
            store = EventStore()
            store.create_session(thread_id, "修复功能", str(ws), status="completed")
            store.append_event(
                thread_id,
                "test_finished",
                "pytest",
                "failed",
                payload={"command": "pytest -q", "status": "failed", "exit_code": 1},
                workspace_dir=str(ws),
            )

            contract = build_delivery_contract(thread_id, str(ws))

            assert contract.status == DeliveryStatus.BLOCKED
            assert any(v.status == "failed" for v in contract.verifications)
            assert any("Verification failed" in r.get("description", "") for r in contract.risks)
