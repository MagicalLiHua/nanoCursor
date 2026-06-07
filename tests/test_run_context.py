import queue
import threading

from src.api.services.run_context import RunContext


def test_run_context_keeps_dict_style_access():
    q = queue.Queue()
    context = RunContext(thread_id="run-1", workspace_dir="/tmp/workspace", queue=q)

    assert context["queue"] is q
    assert context.get("workspace_dir") == "/tmp/workspace"

    context["status"] = "completed"
    context["custom"] = "value"

    assert context.status == "completed"
    assert context.get("custom") == "value"


def test_run_context_binds_conversation_and_metadata():
    context = RunContext(thread_id="run-1", workspace_dir="/tmp/workspace", queue=queue.Queue())

    context.bind_conversation("conv-1", [{"name": "Coder", "role": "coder"}])
    context.set_execution_plan({"strategy": "team_aware_run_per_message"})
    metadata = context.session_metadata()

    assert context.conversation_id == "conv-1"
    assert context.team == [{"name": "Coder", "role": "coder"}]
    assert context.execution_plan == {"strategy": "team_aware_run_per_message"}
    assert metadata["conversation_id"] == "conv-1"
    assert metadata["team"] == [{"name": "Coder", "role": "coder"}]
    assert metadata["execution_plan"] == {"strategy": "team_aware_run_per_message"}
    assert metadata["mode"] == "agenthub_delivery"


def test_run_context_resolves_approval_event():
    approval_event = threading.Event()
    context = RunContext(
        thread_id="demo-1",
        workspace_dir="/tmp/workspace",
        queue=queue.Queue(),
        approval_event=approval_event,
    )

    context.resolve_approval("approved")

    assert context.approval_decision == "approved"
    assert approval_event.is_set()


def test_run_context_advances_lifecycle_from_tool_events():
    context = RunContext(
        thread_id="run-1",
        workspace_dir="/tmp/workspace",
        queue=queue.Queue(),
        execution_plan={
            "stages": [
                {"id": "intake", "title": "Intake", "owner": "Lead", "capabilities": ["tool.memory"]},
                {"id": "plan", "title": "Plan", "owner": "Planner", "capabilities": ["tool.project_index"]},
                {"id": "implement", "title": "Implement", "owner": "Coder", "capabilities": ["tool.file_ops"]},
                {"id": "verify", "title": "Verify", "owner": "Tester", "capabilities": ["skill.delivery-review"]},
            ],
            "tasks": [
                {"id": "stage-01-intake", "title": "Intake"},
                {"id": "stage-02-plan", "title": "Plan"},
                {"id": "stage-03-implement", "title": "Implement"},
                {"id": "stage-04-verify", "title": "Verify"},
            ],
        },
    )

    context.start_first_stage()
    updates = context.apply_tool_event("write_file", "tool.file_ops", agent="Coder", output="ok")
    statuses = {stage["id"]: stage["status"] for stage in context.execution_plan["stages"]}

    assert any(update["stage_id"] == "implement" and update["status"] == "running" for update in updates)
    assert statuses["intake"] == "completed"
    assert statuses["plan"] == "completed"
    assert statuses["implement"] == "running"
    assert context.execution_plan["tasks"][2]["tool_evidence"][0]["tool"] == "write_file"

    context.finalize_lifecycle("completed")
    assert {stage["status"] for stage in context.execution_plan["stages"]} == {"completed"}


def test_run_context_marks_failed_stage_and_skips_remaining():
    context = RunContext(
        thread_id="run-1",
        workspace_dir="/tmp/workspace",
        queue=queue.Queue(),
        execution_plan={
            "stages": [
                {"id": "plan", "title": "Plan", "owner": "Planner", "capabilities": ["tool.project_index"]},
                {"id": "implement", "title": "Implement", "owner": "Coder", "capabilities": ["tool.file_ops"]},
                {"id": "verify", "title": "Verify", "owner": "Tester", "capabilities": ["skill.delivery-review"]},
            ],
            "tasks": [
                {"id": "stage-01-plan", "title": "Plan"},
                {"id": "stage-02-implement", "title": "Implement"},
                {"id": "stage-03-verify", "title": "Verify"},
            ],
        },
    )

    context.start_first_stage()
    context.apply_tool_event("edit_file", "tool.file_ops", agent="Coder", ok=False, output="Error: broken edit")
    context.finalize_lifecycle("failed", "broken edit")
    statuses = {stage["id"]: stage["status"] for stage in context.execution_plan["stages"]}

    assert statuses["plan"] == "completed"
    assert statuses["implement"] == "failed"
    assert statuses["verify"] == "skipped"
    assert context.metadata["lifecycle"]["failed_stage_id"] == "implement"


def test_run_context_recovers_failed_stage_when_later_tool_succeeds():
    context = RunContext(
        thread_id="run-1",
        workspace_dir="/tmp/workspace",
        queue=queue.Queue(),
        execution_plan={
            "stages": [
                {"id": "implement", "title": "Implement", "owner": "Coder", "capabilities": ["tool.file_ops"]},
                {"id": "verify", "title": "Verify", "owner": "Tester", "capabilities": ["skill.delivery-review"]},
            ],
            "tasks": [
                {"id": "stage-01-implement", "title": "Implement"},
                {"id": "stage-02-verify", "title": "Verify"},
            ],
        },
    )

    context.start_first_stage()
    context.apply_tool_event("edit_file", "tool.file_ops", agent="Coder", ok=False, output="Error: bad patch")
    assert context.execution_plan["stages"][0]["status"] == "failed"

    context.apply_tool_event("edit_file", "tool.file_ops", agent="Coder", ok=True, output="Edited README.md")
    context.finalize_lifecycle("completed")
    statuses = {stage["id"]: stage["status"] for stage in context.execution_plan["stages"]}

    assert statuses["implement"] == "completed"
    assert statuses["verify"] == "completed"
    assert context.metadata["lifecycle"]["failed_stage_id"] is None
