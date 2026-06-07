from __future__ import annotations

import asyncio
import uuid

from src.api.models import RunRequest
from src.api.run_state import event_store, run_manager
from src.api.services.run_rate_limit_service import clear_run_start_rate_limit
from src.api.services.run_start_service import start_standard_run


def test_start_standard_run_persists_routing_decision_and_event(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = f"run-start-routing-{uuid.uuid4().hex}"
    clear_run_start_rate_limit()
    try:
        response = asyncio.run(
            start_standard_run(
                RunRequest(
                    prompt="你好",
                    thread_id=thread_id,
                    workspace_dir=str(workspace),
                    team=[{"role": "lead", "name": "Lead"}],
                    execution_plan={"strategy": "analysis_only", "stages": [], "tasks": []},
                ),
                workflow_runner=lambda *_args: None,
            )
        )

        assert response.thread_id == thread_id
        session = event_store.get_session(thread_id, str(workspace))
        assert session is not None
        assert session["routing_decision"]["schema_version"] == "routing-decision-1"
        assert session["routing_decision"]["next_action"] == "answer_directly"
        assert session["execution_plan"]["routing_decision"]["next_action"] == "answer_directly"

        event_types = [event.type for event in event_store.list_events(thread_id, str(workspace))]
        assert "intent_routed" in event_types
        assert "routing_decision_built" in event_types
    finally:
        run_manager.unregister(thread_id)
        clear_run_start_rate_limit()
