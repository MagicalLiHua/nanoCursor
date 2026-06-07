package eventstore

import (
	"testing"
)

func TestCreateAndGetSession(t *testing.T) {
	dir := t.TempDir()
	store := NewStore(dir)
	session := store.CreateSession("t1", "hello", dir, "", "")
	if session.ThreadID != "t1" {
		t.Fatalf("expected t1, got %s", session.ThreadID)
	}
	if session.Status != "running" {
		t.Fatalf("expected running, got %s", session.Status)
	}
	got := store.GetSession("t1", dir)
	if got == nil {
		t.Fatal("expected session")
	}
	if got.Prompt != "hello" {
		t.Fatalf("expected hello, got %s", got.Prompt)
	}
}

func TestUpdateSession(t *testing.T) {
	dir := t.TempDir()
	store := NewStore(dir)
	store.CreateSession("t1", "hello", dir, "", "")
	updated := store.UpdateSession("t1", dir, map[string]string{"status": "completed"})
	if updated == nil {
		t.Fatal("expected updated session")
	}
	if updated.Status != "completed" {
		t.Fatalf("expected completed, got %s", updated.Status)
	}
}

func TestAppendAndListEvents(t *testing.T) {
	dir := t.TempDir()
	store := NewStore(dir)
	store.AppendEvent("t1", "message", "title1", "content1", "lead", "{}", dir)
	store.AppendEvent("t1", "done", "title2", "content2", "lead", "{}", dir)

	events := store.ListEvents("t1", dir, 0)
	if len(events) != 2 {
		t.Fatalf("expected 2 events, got %d", len(events))
	}
	if events[0].Type != "message" {
		t.Fatalf("expected message, got %s", events[0].Type)
	}
	if events[1].Type != "done" {
		t.Fatalf("expected done, got %s", events[1].Type)
	}

	events2 := store.ListEvents("t1", dir, 1)
	if len(events2) != 1 {
		t.Fatalf("expected 1 event after cursor, got %d", len(events2))
	}
	if events2[0].Type != "done" {
		t.Fatalf("expected done, got %s", events2[0].Type)
	}
}

func TestCountEvents(t *testing.T) {
	dir := t.TempDir()
	store := NewStore(dir)
	store.AppendEvent("t1", "message", "", "", "", "", dir)
	store.AppendEvent("t1", "done", "", "", "", "", dir)
	if store.CountEvents("t1", dir) != 2 {
		t.Fatal("expected 2 events")
	}
	if store.CountEvents("t2", dir) != 0 {
		t.Fatal("expected 0 events for unknown thread")
	}
}

func TestWorkspaceForThread(t *testing.T) {
	dir := t.TempDir()
	store := NewStore(dir)
	store.CreateSession("t1", "hello", dir, "", "")
	ws, ok := store.WorkspaceForThread("t1")
	if !ok {
		t.Fatal("expected to find workspace")
	}
	if ws != absPath(dir) {
		t.Fatalf("expected %s, got %s", absPath(dir), ws)
	}
	_, ok = store.WorkspaceForThread("unknown")
	if ok {
		t.Fatal("should not find unknown thread")
	}
}

func TestSubscribeAndNotify(t *testing.T) {
	dir := t.TempDir()
	store := NewStore(dir)
	ch := store.Subscribe("t1")
	store.AppendEvent("t1", "message", "", "", "", "", dir)
	event := <-ch
	if event.Type != "message" {
		t.Fatalf("expected message, got %s", event.Type)
	}
	store.Unsubscribe("t1", ch)
}
