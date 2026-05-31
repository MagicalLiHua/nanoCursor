"""Tests for B5 (ChangeTracker + conflict detection) and B6 (ToolPolicyRuntime adaptation)."""

import json
import os

from src.api.services.change_tracker import ChangeTracker
from src.api.services.parallel_agent_service import _detect_proposal_conflicts
from src.runtime.run_budget import RunBudget
from src.runtime.tool_policy_runtime import ToolPolicyRuntime


# ── B5: ChangeTracker ──


class TestChangeTracker:
    def test_record_and_get_changes(self, tmp_path):
        tracker = ChangeTracker("run-1", str(tmp_path))
        tracker.record_change("src/auth.py", "Coder", "modify")
        tracker.record_change("src/api.py", "Tester", "create")

        changes = tracker.get_changes()
        assert len(changes) == 2
        assert changes[0]["file"] == "src/auth.py"
        assert changes[0]["agent"] == "Coder"
        assert changes[0]["type"] == "modify"
        assert changes[1]["file"] == "src/api.py"

    def test_get_changed_files(self, tmp_path):
        tracker = ChangeTracker("run-1", str(tmp_path))
        tracker.record_change("src/a.py", "Agent1", "modify")
        tracker.record_change("src/b.py", "Agent2", "modify")

        files = tracker.get_changed_files()
        assert files == {"src/a.py", "src/b.py"}

    def test_exclude_agent(self, tmp_path):
        tracker = ChangeTracker("run-1", str(tmp_path))
        tracker.record_change("src/a.py", "Agent1", "modify")
        tracker.record_change("src/b.py", "Agent2", "modify")

        changes = tracker.get_changes(exclude_agent="Agent1")
        assert len(changes) == 1
        assert changes[0]["agent"] == "Agent2"

        files = tracker.get_changed_files(exclude_agent="Agent1")
        assert files == {"src/b.py"}

    def test_build_change_context(self, tmp_path):
        tracker = ChangeTracker("run-1", str(tmp_path))
        tracker.record_change("src/auth.py", "Coder", "modify")

        ctx = tracker.build_change_context()
        assert "src/auth.py" in ctx
        assert "Coder" in ctx

    def test_build_change_context_empty(self, tmp_path):
        tracker = ChangeTracker("run-1", str(tmp_path))
        assert tracker.build_change_context() == ""

    def test_deduplicates_by_file(self, tmp_path):
        tracker = ChangeTracker("run-1", str(tmp_path))
        tracker.record_change("src/a.py", "Agent1", "modify")
        tracker.record_change("src/a.py", "Agent2", "modify")

        ctx = tracker.build_change_context()
        # Should only show the file once (latest entry)
        assert ctx.count("src/a.py") == 1

    def test_handles_missing_dir(self, tmp_path):
        tracker = ChangeTracker("run-1", str(tmp_path / "nonexistent"))
        tracker.record_change("src/a.py", "Agent1", "modify")
        # Should create the dir and record
        changes = tracker.get_changes()
        assert len(changes) == 1


# ── B5: Proposal Conflict Detection ──


class TestDetectProposalConflicts:
    def test_no_conflicts(self):
        proposals = [
            {"name": "Agent1", "suggested_files": ["src/a.py"]},
            {"name": "Agent2", "suggested_files": ["src/b.py"]},
        ]
        assert _detect_proposal_conflicts(proposals) == []

    def test_detects_conflict(self):
        proposals = [
            {"name": "Frontend", "suggested_files": ["src/api.py", "src/ui.js"]},
            {"name": "Backend", "suggested_files": ["src/api.py", "src/db.py"]},
        ]
        conflicts = _detect_proposal_conflicts(proposals)
        assert len(conflicts) == 1
        assert conflicts[0]["file"] == "src/api.py"
        assert set(conflicts[0]["agents"]) == {"Frontend", "Backend"}

    def test_multiple_conflicts(self):
        proposals = [
            {"name": "A", "suggested_files": ["x.py", "y.py"]},
            {"name": "B", "suggested_files": ["x.py", "y.py"]},
        ]
        conflicts = _detect_proposal_conflicts(proposals)
        assert len(conflicts) == 2

    def test_empty_proposals(self):
        assert _detect_proposal_conflicts([]) == []

    def test_empty_suggested_files(self):
        proposals = [
            {"name": "A", "suggested_files": []},
            {"name": "B", "suggested_files": []},
        ]
        assert _detect_proposal_conflicts(proposals) == []


# ── B6: ToolPolicyRuntime Failure Tracking ──


class TestToolPolicyRuntimeAdaptation:
    def test_no_adaptation_on_success(self):
        rt = ToolPolicyRuntime()
        result = rt.record("read_file", ok=True)
        assert result is None
        assert rt.consecutive_failures == 0
        assert rt.success_streak == 1

    def test_tracks_consecutive_failures(self):
        rt = ToolPolicyRuntime()
        rt.record("write_file", ok=False)
        rt.record("write_file", ok=False)
        assert rt.consecutive_failures == 2
        assert rt.success_streak == 0

    def test_resets_failure_streak_on_success(self):
        rt = ToolPolicyRuntime()
        rt.record("write_file", ok=False)
        rt.record("write_file", ok=False)
        rt.record("read_file", ok=True)
        assert rt.consecutive_failures == 0
        assert rt.success_streak == 1

    def test_escalation_after_3_failures(self):
        rt = ToolPolicyRuntime()
        rt.record("write_file", ok=False)
        rt.record("write_file", ok=False)
        result = rt.record("write_file", ok=False)

        assert result is not None
        assert result["type"] == "policy_escalated"
        assert rt._escalated is True
        assert "write_file" in rt.approval_required
        assert "edit_file" in rt.approval_required

    def test_escalation_only_triggers_once(self):
        rt = ToolPolicyRuntime()
        for _ in range(5):
            rt.record("write_file", ok=False)
        # Should only get one escalation event (from the 3rd failure)
        # The 4th and 5th should not re-escalate since _escalated is True

    def test_escalation_blocks_subsequent_writes(self):
        rt = ToolPolicyRuntime()
        # Fail 3 times to trigger escalation
        for _ in range(3):
            rt.record("write_file", ok=False)

        # Now write_file should require approval
        decision = rt.check("write_file", {"file_path": "test.py"})
        assert decision.requires_approval is True
        assert decision.status == "pending"

    def test_escalation_does_not_affect_read_tools(self):
        rt = ToolPolicyRuntime()
        for _ in range(3):
            rt.record("write_file", ok=False)

        decision = rt.check("read_file", {"file_path": "test.py"})
        assert decision.requires_approval is False
        assert decision.status == "auto_allowed"

    def test_budget_boost_after_5_successes(self):
        rt = ToolPolicyRuntime()
        original_max_calls = rt.budget.max_tool_calls
        original_max_writes = rt.budget.max_file_writes

        for i in range(4):
            result = rt.record("read_file", ok=True)
            assert result is None  # No boost yet

        result = rt.record("read_file", ok=True)
        assert result is not None
        assert result["type"] == "policy_relaxed"
        assert rt._budget_boosted is True
        assert rt.budget.max_tool_calls > original_max_calls
        assert rt.budget.max_file_writes > original_max_writes

    def test_budget_boost_only_once(self):
        rt = ToolPolicyRuntime()
        for _ in range(10):
            rt.record("read_file", ok=True)

        # Budget should have been boosted once (after 5 successes)
        # The 6th-10th successes should not trigger another boost

    def test_no_boost_after_escalation(self):
        rt = ToolPolicyRuntime()
        # Fail 3 times to escalate
        for _ in range(3):
            rt.record("write_file", ok=False)
        # Then succeed 5 times
        for _ in range(5):
            rt.record("read_file", ok=True)

        # Should NOT have boosted because escalation already happened
        assert rt._budget_boosted is False

    def test_reset_success_streak_on_failure(self):
        rt = ToolPolicyRuntime()
        for _ in range(4):
            rt.record("read_file", ok=True)
        assert rt.success_streak == 4

        rt.record("write_file", ok=False)
        assert rt.success_streak == 0

    def test_to_dict_includes_adaptation(self):
        rt = ToolPolicyRuntime()
        rt.record("write_file", ok=False)
        rt.record("write_file", ok=False)
        d = rt.to_dict()
        assert "adaptation" in d
        assert d["adaptation"]["consecutive_failures"] == 2
        assert d["adaptation"]["success_streak"] == 0
        assert d["adaptation"]["escalated"] is False

    def test_to_dict_after_escalation(self):
        rt = ToolPolicyRuntime()
        for _ in range(3):
            rt.record("write_file", ok=False)
        d = rt.to_dict()
        assert d["adaptation"]["escalated"] is True
