"""
Tests for src/agent/state.py

Covers:
- WorkflowCancelledError is a distinct exception type
"""

from src.agent.state import WorkflowCancelledError


class TestWorkflowCancelledError:
    """Tests for the cancellation exception."""

    def test_workflow_cancelled_error_is_custom(self):
        """WorkflowCancelledError is a distinct exception type."""
        error = WorkflowCancelledError("test message")
        assert str(error) == "test message"
        assert isinstance(error, Exception)

    def test_workflow_cancelled_error_message(self):
        """WorkflowCancelledError carries the cancellation message."""
        error = WorkflowCancelledError("工作流已被用户取消")
        assert "取消" in str(error)
