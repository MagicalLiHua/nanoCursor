package taskboard

import "testing"

func TestAddAndGetTask(t *testing.T) {
	b := NewRunTaskBoard("run-1", "feature_delivery")
	task := &RunTask{ID: "t1", Type: "analysis", Title: "Analyze", Status: StatusPending, AgentRole: "lead"}
	b.AddTask(task, "test")

	got := b.Task("t1")
	if got == nil {
		t.Fatal("expected to find task t1")
	}
	if got.Title != "Analyze" {
		t.Errorf("title = %q, want %q", got.Title, "Analyze")
	}
}

func TestUpdateTask(t *testing.T) {
	b := NewRunTaskBoard("run-1", "feature_delivery")
	b.AddTask(&RunTask{ID: "t1", Type: "analysis", Title: "Old", Status: StatusPending}, "init")
	b.AddTask(&RunTask{ID: "t1", Type: "analysis", Title: "New", Status: StatusPending}, "update")

	got := b.Task("t1")
	if got.Title != "New" {
		t.Errorf("title = %q, want %q", got.Title, "New")
	}
}

func TestRemoveTask(t *testing.T) {
	b := NewRunTaskBoard("run-1", "feature_delivery")
	b.AddTask(&RunTask{ID: "t1", Type: "analysis", Title: "A", Status: StatusPending}, "")
	b.AddTask(&RunTask{ID: "t2", Type: "test", Title: "B", Status: StatusPending, Dependencies: []string{"t1"}}, "")

	if err := b.RemoveTask("t1", "test"); err != nil {
		t.Fatal(err)
	}
	if b.Task("t1") != nil {
		t.Error("expected t1 to be removed")
	}
	t2 := b.Task("t2")
	if containsString(t2.Dependencies, "t1") {
		t.Error("expected t1 to be removed from t2 dependencies")
	}
}

func TestApplyTaskStatus(t *testing.T) {
	b := NewRunTaskBoard("run-1", "feature_delivery")
	b.AddTask(&RunTask{ID: "t1", Type: "analysis", Title: "A", Status: StatusPending}, "")
	b.AddTask(&RunTask{ID: "t2", Type: "test", Title: "B", Status: StatusPending, Dependencies: []string{"t1"}}, "")

	b.ApplyTaskStatus("t1", StatusFailed)
	if b.Task("t2").Status != StatusBlocked {
		t.Errorf("t2 status = %q, want %q", b.Task("t2").Status, StatusBlocked)
	}
}

func TestReadyNodes(t *testing.T) {
	b := NewRunTaskBoard("run-1", "feature_delivery")
	b.AddTask(&RunTask{ID: "t1", Type: "analysis", Title: "A", Status: StatusPassed}, "")
	b.AddTask(&RunTask{ID: "t2", Type: "test", Title: "B", Status: StatusPending, Dependencies: []string{"t1"}}, "")
	b.AddTask(&RunTask{ID: "t3", Type: "test", Title: "C", Status: StatusPending, Dependencies: []string{"t2"}}, "")

	ready := b.ReadyNodes()
	if len(ready) != 1 || ready[0].ID != "t2" {
		t.Errorf("expected [t2], got %v", ready)
	}
	if ready[0].Status != StatusReady {
		t.Errorf("t2 status = %q, want %q", ready[0].Status, StatusReady)
	}
}

func TestConnectDisconnect(t *testing.T) {
	b := NewRunTaskBoard("run-1", "feature_delivery")
	b.AddTask(&RunTask{ID: "t1", Type: "analysis", Title: "A", Status: StatusPending}, "")
	b.AddTask(&RunTask{ID: "t2", Type: "test", Title: "B", Status: StatusPending}, "")

	b.ConnectTasks("t1", "t2", "test")
	if !containsString(b.Task("t2").Dependencies, "t1") {
		t.Error("expected t1 in t2 dependencies")
	}

	b.DisconnectTasks("t1", "t2", "test")
	if containsString(b.Task("t2").Dependencies, "t1") {
		t.Error("expected t1 removed from t2 dependencies")
	}
}

func TestBoardManager(t *testing.T) {
	m := NewBoardManager()
	b1 := m.GetOrCreate("run-1", "feature_delivery")
	b2 := m.GetOrCreate("run-1", "feature_delivery")
	if b1 != b2 {
		t.Error("expected same board instance")
	}
	if m.Count() != 1 {
		t.Errorf("count = %d, want 1", m.Count())
	}
}
