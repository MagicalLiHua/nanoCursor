"""Event schema tests — typed payloads, normalization, emit, uniqueness."""

import json

import pytest

from src.runtime.event_schema import (
    RunEvent,
    ToolCallPayload,
    StageUpdatedPayload,
    FileChangedPayload,
    ApprovalPayload,
    SCHEMA_VERSION,
    normalize_event,
    validate_event_payload,
)
from src.api.services.event_service import (
    emit_event,
    get_normalized_events,
    build_event_summary,
)
from src.api.services.event_store import get_event_store


# ---------------------------------------------------------------------------
# Payload model validation
# ---------------------------------------------------------------------------

class TestPayloadModels:
    def test_tool_call_payload_defaults(self):
        p = ToolCallPayload(tool="bash", input={"command": "ls"})
        assert p.tool == "bash"
        assert p.ok is True
        assert p.stage_id == ""

    def test_tool_call_payload_with_output(self):
        p = ToolCallPayload(tool="write_file", output="file written", ok=True)
        assert p.output == "file written"

    def test_stage_updated_payload(self):
        p = StageUpdatedPayload(stage_id="implement", status="completed", progress=100)
        assert p.stage_id == "implement"
        assert p.status == "completed"
        assert p.progress == 100

    def test_file_changed_payload(self):
        p = FileChangedPayload(path="app/main.py", change_type="modified", tool="write_file")
        assert p.path == "app/main.py"

    def test_approval_payload(self):
        p = ApprovalPayload(plan_id="plan_1", decision="approved", required_by="lead")
        assert p.decision == "approved"


# ---------------------------------------------------------------------------
# validate_event_payload
# ---------------------------------------------------------------------------

class TestValidatePayload:
    def test_matching_event_type_validates(self):
        result = validate_event_payload("tool_call_finished", {
            "tool": "bash", "input": {}, "output": "ok", "ok": True,
        })
        assert result["tool"] == "bash"

    def test_unknown_event_type_passes_through(self):
        payload = {"custom": "data"}
        result = validate_event_payload("custom_event", payload)
        assert result is payload  # unchanged

    def test_invalid_payload_returns_original(self):
        """Malformed payload that doesn't match model returns original dict."""
        result = validate_event_payload("tool_call_finished", {"bad": "shape"})
        assert "bad" in result  # passthrough since validation failed gracefully


# ---------------------------------------------------------------------------
# normalize_event
# ---------------------------------------------------------------------------

class TestNormalizeEvent:
    def test_old_event_without_schema_version_gets_default(self):
        old = {"id": "ev1", "type": "done", "thread_id": "t1", "timestamp": 123.0,
               "content": "done"}
        ev = normalize_event(old)
        assert ev.schema_version == "0.x"
        assert ev.id == "ev1"
        assert ev.type == "done"

    def test_missing_fields_get_defaults(self):
        old: dict = {}
        ev = normalize_event(old)
        assert ev.title == ""
        assert ev.content == ""
        assert ev.agent == "system"
        assert ev.payload == {}

    def test_event_type_fallback(self):
        """Uses 'event_type' key when 'type' is missing."""
        old = {"event_type": "file_changed", "thread_id": "t1"}
        ev = normalize_event(old)
        assert ev.type == "file_changed"

    def test_extra_fields_are_stripped(self):
        old = {"id": "ev1", "type": "done", "thread_id": "t1", "extra_junk": "should_be_removed"}
        ev = normalize_event(old)
        assert not hasattr(ev, "extra_junk")
        assert "extra_junk" not in ev.model_dump()


# ---------------------------------------------------------------------------
# emit_event
# ---------------------------------------------------------------------------

class TestEmitEvent:
    def test_emit_event_has_schema_version(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        store = get_event_store()
        ev = emit_event("t1", "test_event", title="Test", workspace_dir=str(ws), event_store=store)
        assert ev.schema_version == SCHEMA_VERSION
        assert ev.id
        assert ev.thread_id == "t1"

    def test_emitted_event_is_persisted(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        store = get_event_store()
        emit_event("t2", "tool_call_finished", title="Bash",
                   payload={"tool": "bash", "input": {}, "ok": True},
                   workspace_dir=str(ws), event_store=store)

        events = store.list_events("t2", str(ws))
        assert len(events) >= 1
        found = [e for e in events if e.type == "tool_call_finished"]
        assert len(found) >= 1


# ---------------------------------------------------------------------------
# Event uniqueness
# ---------------------------------------------------------------------------

class TestEventUniqueness:
    def test_emit_event_creates_unique_ids(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        store = get_event_store()
        ev1 = emit_event("t3", "note", workspace_dir=str(ws), event_store=store)
        ev2 = emit_event("t3", "note", workspace_dir=str(ws), event_store=store)
        assert ev1.id != ev2.id


# ---------------------------------------------------------------------------
# Event summary
# ---------------------------------------------------------------------------

class TestEventSummary:
    def test_build_summary_from_normalized_events(self):
        events = [
            RunEvent(id="1", thread_id="t1", type="tool_call_finished",
                     payload={"tool": "bash", "ok": True}),
            RunEvent(id="2", thread_id="t1", type="tool_call_finished",
                     payload={"tool": "write_file", "ok": False}),
            RunEvent(id="3", thread_id="t1", type="file_changed",
                     payload={"path": "app/main.py"}),
            RunEvent(id="4", thread_id="t1", type="stage_updated",
                     payload={"stage_id": "implement", "status": "completed"}),
        ]
        summary = build_event_summary(events)
        assert summary["event_count"] == 4
        assert summary["tool_calls"] == 2
        assert summary["tool_failures"] == 1
        assert summary["files_changed_count"] == 1
        assert "implement" in summary["stages_touched"]
