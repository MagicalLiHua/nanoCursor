package supervisor

import "testing"

func TestLimiterRejectsAndReleasesByWorkspaceAndRun(t *testing.T) {
	limiter := NewLimiter(1, 1)
	scope := LimitScope{Workspace: "/tmp/project", RunID: "run-1"}

	slot, snapshot, ok := limiter.TryAcquire(scope)
	if !ok {
		t.Fatalf("expected first acquire, got snapshot %#v", snapshot)
	}
	if snapshot.WorkspaceActive != 1 || snapshot.RunActive != 1 {
		t.Fatalf("unexpected active counts: %#v", snapshot)
	}

	if _, snapshot, ok := limiter.TryAcquire(LimitScope{Workspace: "/tmp/project", RunID: "run-2"}); ok {
		t.Fatalf("expected workspace busy, got snapshot %#v", snapshot)
	}
	if _, snapshot, ok := limiter.TryAcquire(LimitScope{Workspace: "/tmp/other", RunID: "run-1"}); ok {
		t.Fatalf("expected run busy, got snapshot %#v", snapshot)
	}

	slot.Release()
	slot.Release()
	if snapshot := limiter.Snapshot(scope); snapshot.WorkspaceActive != 0 || snapshot.RunActive != 0 {
		t.Fatalf("expected released slot, got %#v", snapshot)
	}
}
