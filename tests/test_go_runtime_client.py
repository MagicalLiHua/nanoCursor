"""Contract tests for the optional Go runtime Python adapter."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.runtime.go_runtime_client import normalize_command_result
from src.runtime.command_runner import run_command


def test_normalize_go_command_contract_success():
    fixture = Path("tests/contracts/go_runtime/command_success.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    result = normalize_command_result(payload)

    assert result["backend"] == "go_runtime"
    assert result["command"] == "echo hello"
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello\n"
    assert result["stdout_truncated"] is False
    assert result["timed_out"] is False
    assert result["tool_run_id"] == "tr_contract"
    assert result["runtime_events"] == []


def test_run_command_via_go_runtime_fetches_events(monkeypatch, tmp_path):
    from src.runtime import go_runtime_client as client

    calls = []
    event_calls = []

    def fake_post(path, payload):
        calls.append((path, payload))
        return {"tool_run_id": "tr_1", "status": "running"}

    def fake_get(path):
        calls.append((path, None))
        if path == "/v1/tools/runs/tr_1":
            return {
                "tool_run_id": "tr_1",
                "status": "completed",
                "backend": "go_runtime",
                "command": "echo hi",
                "cwd": str(tmp_path),
                "exit_code": 0,
                "stdout": "hi\n",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
                "duration_ms": 1,
                "timed_out": False,
            }
        if path == "/v1/tools/runs/tr_1/events?after=0":
            return {
                "events": [{"id": "evt_1", "type": "tool.stdout", "payload": {"text": "hi\n"}}],
                "cursor": 1,
            }
        if path == "/v1/tools/runs/tr_1/events?after=1":
            return {"events": [], "cursor": 1}
        raise AssertionError(path)

    monkeypatch.setattr(client, "_post_json", fake_post)
    monkeypatch.setattr(client, "_get_json", fake_get)

    result = client.run_command_via_go_runtime(
        "echo hi",
        tmp_path,
        run_id="run_1",
        on_runtime_event=event_calls.append,
    )

    assert result["backend"] == "go_runtime"
    assert result["runtime_events"][0]["type"] == "tool.stdout"
    assert event_calls[0]["id"] == "evt_1"
    assert calls[0][1]["run_id"] == "run_1"


def test_run_command_via_go_runtime_async_fetches_events(monkeypatch, tmp_path):
    from src.runtime import go_runtime_client as client

    event_calls = []

    def fake_post(path, payload):
        return {"tool_run_id": "tr_async", "status": "running"}

    def fake_get(path):
        if path == "/v1/tools/runs/tr_async":
            return {
                "tool_run_id": "tr_async",
                "status": "completed",
                "backend": "go_runtime",
                "command": "echo async",
                "cwd": str(tmp_path),
                "exit_code": 0,
                "stdout": "async\n",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
                "duration_ms": 1,
                "timed_out": False,
            }
        if path == "/v1/tools/runs/tr_async/events?after=0":
            return {
                "events": [{"id": "evt_async", "type": "tool.stdout", "payload": {"text": "async\n"}}],
                "cursor": 1,
            }
        if path == "/v1/tools/runs/tr_async/events?after=1":
            return {"events": [], "cursor": 1}
        raise AssertionError(path)

    monkeypatch.setattr(client, "_post_json", fake_post)
    monkeypatch.setattr(client, "_get_json", fake_get)

    result = asyncio.run(
        client.run_command_via_go_runtime_async(
            "echo async",
            tmp_path,
            on_runtime_event=event_calls.append,
        )
    )

    assert result["backend"] == "go_runtime"
    assert result["exit_code"] == 0
    assert result["runtime_events"][0]["id"] == "evt_async"
    assert event_calls[0]["type"] == "tool.stdout"


def test_run_command_via_go_runtime_polls_events_before_terminal(monkeypatch, tmp_path):
    from src.runtime import go_runtime_client as client

    run_polls = 0
    seen_events = []

    def fake_post(path, payload):
        return {"tool_run_id": "tr_stream", "status": "running"}

    def fake_get(path):
        nonlocal run_polls
        if path == "/v1/tools/runs/tr_stream":
            run_polls += 1
            status = "running" if run_polls == 1 else "completed"
            return {
                "tool_run_id": "tr_stream",
                "status": status,
                "backend": "go_runtime",
                "command": "printf hi",
                "cwd": str(tmp_path),
                "exit_code": 0,
                "stdout": "hi\n" if status == "completed" else "",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
                "duration_ms": 1,
                "timed_out": False,
            }
        if path == "/v1/tools/runs/tr_stream/events?after=0":
            return {"events": [{"id": "evt_live", "type": "tool.stdout", "payload": {"text": "hi\n"}}], "cursor": 1}
        if path == "/v1/tools/runs/tr_stream/events?after=1":
            return {"events": [{"id": "evt_done", "type": "tool.completed", "payload": {"exit_code": 0}}], "cursor": 2}
        if path == "/v1/tools/runs/tr_stream/events?after=2":
            return {"events": [], "cursor": 2}
        raise AssertionError(path)

    monkeypatch.setattr(client, "_post_json", fake_post)
    monkeypatch.setattr(client, "_get_json", fake_get)

    result = client.run_command_via_go_runtime(
        "printf hi",
        tmp_path,
        on_runtime_event=seen_events.append,
        event_poll_interval_seconds=0.02,
    )

    assert [event["id"] for event in seen_events] == ["evt_live", "evt_done"]
    assert [event["id"] for event in result["runtime_events"]] == ["evt_live", "evt_done"]


def test_normalize_go_policy_denied_contract():
    fixture = Path("tests/contracts/go_runtime/policy_denied.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    result = normalize_command_result(payload)

    assert result["backend"] == "go_runtime"
    assert result["exit_code"] == -1
    assert result["status"] == "denied"
    assert result["error_code"] == "approval_required"
    assert "approval token" in result["stderr"]


def test_command_runner_falls_back_when_go_runtime_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("NANOCURSOR_GO_RUNTIME_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("NANOCURSOR_GO_RUNTIME_TIMEOUT_MS", "1000")

    result = run_command("echo fallback", cwd=tmp_path)

    assert result["backend"] == "python_subprocess"
    assert result["exit_code"] == 0
    assert "fallback" in result["stdout"]
