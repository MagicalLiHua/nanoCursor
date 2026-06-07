package cron

import (
	"fmt"
	"path/filepath"
	"testing"
	"time"
)

func TestSchedulerCreateAndList(t *testing.T) {
	s := NewScheduler("")
	id := s.Create("*/5 * * * *", "test prompt", false, false)
	if id == "" {
		t.Fatal("expected non-empty ID")
	}
	tasks := s.ListAll()
	if len(tasks) != 1 {
		t.Fatalf("expected 1 task, got %d", len(tasks))
	}
	if tasks[0].Prompt != "test prompt" {
		t.Fatalf("expected 'test prompt', got %s", tasks[0].Prompt)
	}
}

func TestSchedulerDelete(t *testing.T) {
	s := NewScheduler("")
	id := s.Create("*/5 * * * *", "to delete", false, false)
	if !s.Delete(id) {
		t.Fatal("delete should return true")
	}
	if len(s.ListAll()) != 0 {
		t.Fatal("should have 0 tasks after delete")
	}
	if s.Delete("nonexistent") {
		t.Fatal("delete nonexistent should return false")
	}
}

func TestSchedulerDrainEvents(t *testing.T) {
	s := NewScheduler("")
	s.mu.Lock()
	s.events = append(s.events, CronEvent{Type: "test", TaskID: "t1"})
	s.mu.Unlock()
	select {
	case s.eventCh <- struct{}{}:
	default:
	}

	events := s.DrainEvents()
	if len(events) != 1 {
		t.Fatalf("expected 1 event, got %d", len(events))
	}

	s.mu.Lock()
	s.events = append(s.events, CronEvent{Type: "test2", TaskID: "t2"})
	s.mu.Unlock()
	select {
	case s.eventCh <- struct{}{}:
	default:
	}
	events = s.DrainEvents()
	if len(events) != 1 || events[0].Type != "test2" {
		t.Fatalf("expected fresh event, got %v", events)
	}
}

func TestSchedulerPersistence(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tasks.json")
	s := NewScheduler(path)
	s.Create("*/5 * * * *", "durable task", true, true)
	s.Create("*/5 * * * *", "ephemeral task", false, false)

	s2 := NewScheduler(path)
	s2.LoadFromFile()
	tasks := s2.ListAll()
	if len(tasks) != 1 {
		t.Fatalf("expected 1 durable task, got %d", len(tasks))
	}
	if tasks[0].Prompt != "durable task" {
		t.Fatalf("expected 'durable task', got %s", tasks[0].Prompt)
	}
}

func TestSchedulerFireNonRecurring(t *testing.T) {
	s := NewScheduler("")
	now := time.Now()
	expr := fmt.Sprintf("%d %d * * *", now.Minute(), now.Hour())
	s.Create(expr, "one-shot", false, false)

	s.Start()
	time.Sleep(2 * time.Second)
	s.Stop()

	tasks := s.ListAll()
	if len(tasks) != 0 {
		t.Fatalf("expected 0 tasks after non-recurring fire, got %d", len(tasks))
	}
}
