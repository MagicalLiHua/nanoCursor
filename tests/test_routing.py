"""
Tests for routing logic in src/core/routing.py and run.py::route_after_sandbox.

Covers:
- route_after_planner: tool_call vs no-tool_call routing
- route_after_coder: tool_call, max_steps, completion routing
- route_after_sandbox (from run.py): success, retry, max_retries routing
"""

from langchain_core.messages import AIMessage

# ---------------------------------------------------------------------------
# route_after_planner
# ---------------------------------------------------------------------------


class TestRouteAfterPlanner:
    def test_routes_to_tools_when_tool_calls_present(self):
        from src.core.routing import route_after_planner
        ai = AIMessage(content="")
        ai.tool_calls = [{"name": "read_file", "args": {"filename": "x.py"}}]
        state = {"messages": [ai]}
        assert route_after_planner(state) == "planner_tools"

    def test_routes_to_coder_when_done(self):
        from src.core.routing import route_after_planner
        # No tool_calls -> plan is ready, go to coder.
        ai = AIMessage(content="Here is the plan...")
        state = {"messages": [ai]}
        assert route_after_planner(state) == "coder"


# ---------------------------------------------------------------------------
# route_after_coder
# ---------------------------------------------------------------------------


class TestRouteAfterCoder:
    def test_routes_to_counter_when_tool_calls(self):
        from src.core.routing import route_after_coder
        ai = AIMessage(content="")
        ai.tool_calls = [{"name": "read_file", "args": {"filename": "x.py"}}]
        state = {"messages": [ai], "coder_step_count": 0, "max_coder_steps": 15}
        assert route_after_coder(state) == "coder_step_counter"

    def test_routes_to_sandbox_when_done(self):
        from src.core.routing import route_after_coder
        ai = AIMessage(content="DONE")  # no tool_calls
        state = {"messages": [ai], "coder_step_count": 0, "max_coder_steps": 15}
        assert route_after_coder(state) == "sandbox"

    def test_routes_to_sandbox_at_max_steps(self):
        from src.core.routing import route_after_coder
        ai = AIMessage(content="")
        ai.tool_calls = [{"name": "write_file", "args": {"filename": "x.py"}}]
        state = {"messages": [ai], "coder_step_count": 15, "max_coder_steps": 15}
        assert route_after_coder(state) == "sandbox"

    def test_max_coder_steps_from_env(self, monkeypatch):
        from src.core.routing import route_after_coder
        monkeypatch.setenv("MAX_CODER_STEPS", "5")
        ai = AIMessage(content="")
        ai.tool_calls = [{"name": "read", "args": {"filename": "x.py"}}]
        state = {"messages": [ai], "coder_step_count": 5, "max_coder_steps": 5}
        assert route_after_coder(state) == "sandbox"

    def test_below_max_goes_to_counter(self):
        from src.core.routing import route_after_coder
        ai = AIMessage(content="")
        ai.tool_calls = [{"name": "edit_file", "args": {"filename": "x.py"}}]
        state = {"messages": [ai], "coder_step_count": 3, "max_coder_steps": 10}
        assert route_after_coder(state) == "coder_step_counter"


# ---------------------------------------------------------------------------
# route_after_sandbox (canonical version lives in run.py)
# ---------------------------------------------------------------------------


class TestRouteAfterSandbox:
    """The actual route_after_sandbox with snapshot side-effect is in run.py.
    We test it directly from there to match production behavior."""

    def _get_func(self):
        from run import route_after_sandbox
        return route_after_sandbox

    def test_success_routes_to_end(self):
        route_after_sandbox = self._get_func()
        state = {
            "error_trace": "",
            "retry_count": 0,
            "max_retries": 3,
        }
        result = route_after_sandbox(state)
        from langgraph.graph import END
        assert result == END

    def test_retry_below_max_routes_to_reviewer(self):
        route_after_sandbox = self._get_func()
        state = {
            "error_trace": "Traceback: TypeError",
            "retry_count": 1,
            "max_retries": 3,
        }
        result = route_after_sandbox(state)
        assert result == "reviewer"

    def test_retry_at_max_routes_to_end(self):
        route_after_sandbox = self._get_func()
        state = {
            "error_trace": "Traceback: TypeError",
            "retry_count": 3,
            "max_retries": 3,
        }
        result = route_after_sandbox(state)
        from langgraph.graph import END
        assert result == END
