package tools

import (
	"testing"
	"time"
)

func TestManagerExecuteCommand(t *testing.T) {
	manager := NewManager()
	var req CommandRequest
	req.WorkspaceDir = t.TempDir()
	req.Tool = "run_command"
	req.Input.Command = "echo hello"
	req.Input.TimeoutMS = 5000
	req.Policy.PermissionLevel = "shell_safe"

	started, err := manager.Execute(req)
	if err != nil {
		t.Fatal(err)
	}
	if started.Status != "running" {
		t.Fatalf("expected running, got %s", started.Status)
	}

	var run *ToolRun
	for range 50 {
		current, ok := manager.Get(started.ToolRunID)
		if !ok {
			t.Fatal("run missing")
		}
		run = current
		if run.Status != "running" {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if run == nil || run.Status != "completed" {
		t.Fatalf("expected completed run, got %#v", run)
	}
	if run.ExitCode != 0 {
		t.Fatalf("expected exit 0, got %d", run.ExitCode)
	}
	if run.Backend != "go_runtime" {
		t.Fatalf("expected go_runtime backend, got %s", run.Backend)
	}
	if run.Stdout == "" {
		t.Fatal("expected stdout")
	}
	events, ok := manager.EventsAfter(started.ToolRunID, 0)
	if !ok {
		t.Fatal("expected events")
	}
	if len(events) == 0 {
		t.Fatal("expected at least one event")
	}
	tail, ok := manager.EventsAfter(started.ToolRunID, 1)
	if !ok {
		t.Fatal("expected tail events")
	}
	if len(tail) != len(events)-1 {
		t.Fatalf("expected cursor to skip one event, got %d of %d", len(tail), len(events))
	}
	beyond, ok := manager.EventsAfter(started.ToolRunID, len(events)+10)
	if !ok {
		t.Fatal("expected beyond cursor events")
	}
	if len(beyond) != 0 {
		t.Fatalf("expected empty events beyond cursor, got %#v", beyond)
	}
}

func TestManagerDeniesRiskyCommand(t *testing.T) {
	manager := NewManager()
	var req CommandRequest
	req.WorkspaceDir = t.TempDir()
	req.Input.Command = "rm -rf dist"

	run, err := manager.Execute(req)
	if err != nil {
		t.Fatal(err)
	}
	if run.Status != "denied" {
		t.Fatalf("expected denied, got %s", run.Status)
	}
	if run.ErrorCode != "approval_required" {
		t.Fatalf("expected approval_required, got %s", run.ErrorCode)
	}
}

func TestManagerRejectsWhenWorkspaceBusy(t *testing.T) {
	manager := newManagerWithLimits(1, 1)
	workspace := t.TempDir()
	var first CommandRequest
	first.WorkspaceDir = workspace
	first.RunID = "run-a"
	first.Tool = "run_command"
	first.Input.Command = "sleep 0.3"
	first.Input.TimeoutMS = 2000
	first.Policy.PermissionLevel = "shell_safe"

	started, err := manager.Execute(first)
	if err != nil {
		t.Fatal(err)
	}
	if started.Status != "running" {
		t.Fatalf("expected first command running, got %s", started.Status)
	}

	second := first
	second.RunID = "run-b"
	busy, err := manager.Execute(second)
	if err != nil {
		t.Fatal(err)
	}
	if busy.Status != "failed" || busy.ErrorCode != "runtime_busy" {
		t.Fatalf("expected runtime_busy failed run, got %#v", busy)
	}
	events, ok := manager.EventsAfter(busy.ToolRunID, 0)
	if !ok || len(events) != 1 || events[0].Type != "runtime.busy" {
		t.Fatalf("expected runtime.busy event, got %#v", events)
	}

	run := waitForTerminalRun(t, manager, started.ToolRunID)
	if run.Status != "completed" {
		t.Fatalf("expected first run to complete, got %#v", run)
	}

	third := first
	third.RunID = "run-c"
	afterRelease, err := manager.Execute(third)
	if err != nil {
		t.Fatal(err)
	}
	if afterRelease.Status != "running" {
		t.Fatalf("expected slot to be released, got %#v", afterRelease)
	}
	_ = waitForTerminalRun(t, manager, afterRelease.ToolRunID)
}

func waitForTerminalRun(t *testing.T, manager *Manager, toolRunID string) *ToolRun {
	t.Helper()
	for range 80 {
		run, ok := manager.Get(toolRunID)
		if !ok {
			t.Fatal("run missing")
		}
		if run.Status != "running" {
			return run
		}
		time.Sleep(20 * time.Millisecond)
	}
	run, _ := manager.Get(toolRunID)
	t.Fatalf("run did not finish: %#v", run)
	return nil
}
