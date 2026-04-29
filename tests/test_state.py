"""
Tests for src/core/state.py

Covers:
- AgentState TypedDict structure
- check_cancelled raises WorkflowCancelledError when cancelled=True
"""

import pytest
from langchain_core.messages import AIMessage

from src.core.state import AgentState, WorkflowCancelledError, check_cancelled


class TestCheckCancelled:
    """Tests for the cancellation mechanism."""

    def test_does_not_raise_when_cancelled_false(self):
        """check_cancelled is a no-op when cancelled=False."""
        state: AgentState = {
            "messages": [],
            "cancelled": False,
        }
        # Should not raise
        check_cancelled(state)

    def test_does_not_raise_when_cancelled_missing(self):
        """check_cancelled is a no-op when cancelled key is absent."""
        state: AgentState = {
            "messages": [],
        }
        check_cancelled(state)

    def test_raises_when_cancelled_true(self):
        """check_cancelled raises WorkflowCancelledError when cancelled=True."""
        state: AgentState = {
            "messages": [],
            "cancelled": True,
        }
        with pytest.raises(WorkflowCancelledError) as exc_info:
            check_cancelled(state)
        assert "取消" in str(exc_info.value)

    def test_workflow_cancelled_error_is_custom(self):
        """WorkflowCancelledError is a distinct exception type."""
        error = WorkflowCancelledError("test message")
        assert str(error) == "test message"
        assert isinstance(error, Exception)

    def test_agent_state_accepts_all_fields(self):
        """AgentState can be constructed with all expected fields."""
        state: AgentState = {
            "messages": [AIMessage(content="test")],
            "current_plan": "write tests",
            "active_files": ["test.py"],
            "error_trace": "",
            "retry_count": 0,
            "max_retries": 3,
            "modification_log": [],
            "memory_summary": {"original_request": "test"},
            "context_version": 1,
            "file_signatures": {},
            "coder_step_count": 0,
            "max_coder_steps": 15,
            "cancelled": False,
        }
        assert state["coder_step_count"] == 0
        assert state["cancelled"] is False
