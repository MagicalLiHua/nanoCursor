package supervisor

import (
	"context"
	"io"
	"os"
	"strings"
	"testing"
	"time"
)

func TestStartStdioProcessRoundTrip(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	events := []ProcessEvent{}
	process, err := StartStdioProcess(ctx, StdioProcessSpec{
		Kind:    "test_stdio",
		Command: os.Args[0],
		Args:    []string{"-test.run=TestHelperStdioProcess", "--"},
		Env:     map[string]string{"NANOCURSOR_HELPER_STDIO": "1"},
	}, func(event ProcessEvent) {
		events = append(events, event)
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := process.Stdin().Write([]byte("hello")); err != nil {
		t.Fatal(err)
	}
	if err := process.Stdin().Close(); err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(process.Stdout())
	if err != nil {
		t.Fatal(err)
	}
	if string(body) != "echo:hello" {
		t.Fatalf("unexpected stdout: %q", string(body))
	}
	if err := process.Wait(); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(process.StderrString(), "helper warning") {
		t.Fatalf("expected captured stderr, got %q", process.StderrString())
	}
	if len(events) < 3 {
		t.Fatalf("expected lifecycle and stderr events, got %#v", events)
	}
	if events[0].Type != "tool.started" || events[len(events)-1].Type != "tool.completed" {
		t.Fatalf("unexpected events: %#v", events)
	}
}

func TestHelperStdioProcess(t *testing.T) {
	if os.Getenv("NANOCURSOR_HELPER_STDIO") != "1" {
		return
	}
	body, _ := io.ReadAll(os.Stdin)
	_, _ = os.Stderr.WriteString("helper warning\n")
	_, _ = os.Stdout.WriteString("echo:" + string(body))
	os.Exit(0)
}
