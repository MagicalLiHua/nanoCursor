"""Change set model and service tests."""

import os
import subprocess
import tempfile
from pathlib import Path

from src.runtime.change_set import ChangeSet, ChangeSetStatus, FilePatchSummary
from src.api.services.change_service import (
    _classify_risk,
    _is_git_repo,
    _parse_numstat,
    _parse_status,
    collect_changes,
    collect_changes_git,
    load_change_set,
    review_changes,
    save_change_set,
)


class TestFilePatchSummary:
    def test_default_values(self):
        fp = FilePatchSummary(path="src/main.py")
        assert fp.change_type == "modified"
        assert fp.additions == 0
        assert fp.deletions == 0
        assert fp.hunks == 0
        assert fp.risk == "medium"

    def test_full_fields(self):
        fp = FilePatchSummary(
            path="src/app.py",
            change_type="modified",
            additions=25,
            deletions=10,
            hunks=3,
            summary="Refactored init",
            risk="medium",
            related_requirement_ids=["req-1"],
        )
        d = fp.model_dump()
        assert d["additions"] == 25
        assert d["related_requirement_ids"] == ["req-1"]


class TestChangeSetModel:
    def test_default_status(self):
        cs = ChangeSet(thread_id="run_1", workspace_dir="/tmp/ws")
        assert cs.status == ChangeSetStatus.COLLECTED
        assert cs.files == []
        assert cs.total_additions == 0

    def test_with_files(self):
        cs = ChangeSet(
            thread_id="run_2",
            workspace_dir="/tmp/ws",
            files=[
                FilePatchSummary(path="a.py", additions=10, deletions=2, hunks=1, risk="low"),
                FilePatchSummary(path="b.py", additions=5, deletions=0, hunks=1, risk="medium"),
            ],
            total_additions=15,
            total_deletions=2,
            status=ChangeSetStatus.REVIEWED,
        )
        assert len(cs.files) == 2
        assert cs.total_additions == 15


class TestRiskClassification:
    def test_deleted_file_high(self):
        assert _classify_risk("src/old.py", "deleted", 0, 0) == "high"

    def test_large_change_high(self):
        assert _classify_risk("src/big.py", "modified", 300, 250) == "high"

    def test_lockfile_high(self):
        assert _classify_risk("package-lock.json", "modified", 50, 50) == "high"
        assert _classify_risk("yarn.lock", "modified", 10, 10) == "high"

    def test_env_file_high(self):
        assert _classify_risk(".env", "modified", 3, 1) == "high"

    def test_ci_config_medium(self):
        assert _classify_risk(".github/workflows/ci.yml", "modified", 10, 5) == "medium"
        assert _classify_risk("Jenkinsfile", "modified", 5, 2) == "medium"
        assert _classify_risk("Dockerfile", "modified", 3, 1) == "medium"

    def test_config_file_medium(self):
        assert _classify_risk("app.toml", "modified", 5, 3) == "medium"
        assert _classify_risk("settings.yaml", "modified", 10, 0) == "medium"

    def test_test_file_low(self):
        assert _classify_risk("tests/test_auth.py", "modified", 20, 5) == "low"
        assert _classify_risk("src/__test__/util.py", "modified", 10, 0) == "low"
        assert _classify_risk("spec/models_spec.rb", "modified", 15, 0) == "low"

    def test_normal_file_medium(self):
        assert _classify_risk("src/utils.py", "modified", 30, 10) == "medium"


class TestGitParsing:
    def test_parse_numstat(self):
        output = "10\t5\tsrc/main.py\n3\t0\tREADME.md\n"
        result = _parse_numstat(output)
        assert result["src/main.py"] == (10, 5)
        assert result["README.md"] == (3, 0)

    def test_parse_numstat_binary(self):
        output = "-\t-\timage.png\n5\t2\tcode.py\n"
        result = _parse_numstat(output)
        assert result["image.png"] == (0, 0)
        assert result["code.py"] == (5, 2)

    def test_parse_status(self):
        output = "M  src/main.py\nA  src/new.py\nD  src/old.py\n?? untracked.py\n"
        result = _parse_status(output)
        assert result["src/main.py"] == "modified"
        assert result["src/new.py"] == "added"
        assert result["src/old.py"] == "deleted"
        assert result["untracked.py"] == "added"


class TestChangeSetPersistence:
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            cs = ChangeSet(
                thread_id="run_cs_001",
                workspace_dir=str(ws),
                status=ChangeSetStatus.COLLECTED,
                files=[
                    FilePatchSummary(path="src/a.py", additions=5, deletions=1, risk="low"),
                ],
                total_additions=5,
                total_deletions=1,
                generated_at="2026-05-18T12:00:00Z",
            )
            path = save_change_set(cs)
            assert path.exists()

            loaded = load_change_set("run_cs_001", str(ws))
            assert loaded is not None
            assert loaded.thread_id == "run_cs_001"
            assert len(loaded.files) == 1

    def test_load_missing_returns_none(self):
        assert load_change_set("nonexistent_cs_xyz") is None

    def test_load_corrupt_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            rd = ws / ".nanocursor" / "runs" / "bad_cs"
            rd.mkdir(parents=True)
            (rd / "changes.json").write_text("not valid {{{", encoding="utf-8")
            assert load_change_set("bad_cs", str(ws)) is None


class TestCollectChangesGit:
    def test_collect_in_git_repo(self, tmp_path):
        """Test change collection in a real git repo."""
        ws = tmp_path / "repo"
        ws.mkdir(parents=True)

        # Init git repo
        subprocess.run(["git", "init"], cwd=ws, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=ws, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, capture_output=True)

        # Create and commit initial file
        (ws / "README.md").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=ws, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=ws, capture_output=True)

        # Modify and add files
        (ws / "README.md").write_text("hello world")
        (ws / "src").mkdir(exist_ok=True)
        (ws / "src" / "main.py").write_text("print('ok')")
        subprocess.run(["git", "add", "src/main.py"], cwd=ws, capture_output=True)

        files = collect_changes_git(ws)
        assert len(files) >= 1
        paths = {f.path for f in files}
        assert "README.md" in paths or "src/main.py" in paths

    def test_collect_non_git_fallback(self, tmp_path):
        ws = tmp_path / "non_git"
        ws.mkdir(parents=True)
        (ws / "file.txt").write_text("content")

        cs = collect_changes("run_nogit", str(ws))
        assert cs.thread_id == "run_nogit"
        assert cs.status == ChangeSetStatus.COLLECTED
        # Non-git with no checkpoints returns empty
        assert isinstance(cs.files, list)


class TestReviewChanges:
    def test_review_sets_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            cs = ChangeSet(
                thread_id="run_review",
                workspace_dir=str(ws),
                files=[
                    FilePatchSummary(path="deleted.py", change_type="deleted", risk="medium"),
                ],
            )
            save_change_set(cs)
            reviewed = review_changes("run_review", str(ws))
            assert reviewed.status == ChangeSetStatus.REVIEWED
            # Risk should be re-evaluated: deleted → high
            assert reviewed.files[0].risk == "high"
