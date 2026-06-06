package supervisor

import (
	"context"
	"sync"
	"testing"
	"time"
)

func TestRunCommandCapturesOutputAndEvents(t *testing.T) {
	events := []ProcessEvent{}
	var eventsMu sync.Mutex
	result := RunCommand(
		context.Background(),
		ProcessSpec{
			Kind:           "run_command",
			Command:        "echo hello",
			Cwd:            t.TempDir(),
			MaxStdoutChars: 1000,
			MaxStderrChars: 1000,
		},
		func(event ProcessEvent) {
			eventsMu.Lock()
			defer eventsMu.Unlock()
			events = append(events, event)
		},
	)

	if result.Status != "completed" {
		t.Fatalf("expected completed, got %#v", result)
	}
	if result.ExitCode != 0 {
		t.Fatalf("expected exit 0, got %#v", result)
	}
	if result.Stdout == "" {
		t.Fatalf("expected stdout, got %#v", result)
	}
	eventsMu.Lock()
	defer eventsMu.Unlock()
	if len(events) < 2 {
		t.Fatalf("expected lifecycle events, got %#v", events)
	}
	if events[0].Type != "tool.started" {
		t.Fatalf("expected started event first, got %#v", events)
	}
	if events[len(events)-1].Type != "tool.completed" {
		t.Fatalf("expected completed event last, got %#v", events)
	}
}

func TestRunCommandTruncatesLongOutput(t *testing.T) {
	result := RunCommand(
		context.Background(),
		ProcessSpec{
			Kind:           "run_command",
			Command:        "printf 'abcdef'",
			Cwd:            t.TempDir(),
			MaxStdoutChars: 3,
			MaxStderrChars: 1000,
		},
		nil,
	)

	if result.Status != "completed" {
		t.Fatalf("expected completed, got %#v", result)
	}
	if result.Stdout != "abc" {
		t.Fatalf("expected truncated stdout, got %q", result.Stdout)
	}
	if !result.StdoutTruncated {
		t.Fatalf("expected stdout truncated, got %#v", result)
	}
	if result.StdoutBytes < 6 {
		t.Fatalf("expected full byte count, got %#v", result)
	}
}

func TestRunCommandTimeout(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	result := RunCommand(
		ctx,
		ProcessSpec{
			Kind:           "run_command",
			Command:        "sleep 2",
			Cwd:            t.TempDir(),
			MaxStdoutChars: 1000,
			MaxStderrChars: 1000,
		},
		nil,
	)

	if result.Status != "timeout" {
		t.Fatalf("expected timeout, got %#v", result)
	}
	if !result.TimedOut {
		t.Fatalf("expected timed_out true, got %#v", result)
	}
}
