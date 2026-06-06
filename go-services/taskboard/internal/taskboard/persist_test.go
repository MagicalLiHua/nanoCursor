package taskboard

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestSaveAndLoadBoard(t *testing.T) {
	dir := t.TempDir()
	board := NewRunTaskBoard("run-1", "feature_delivery")
	board.AddTask(&RunTask{ID: "t1", Type: "analysis", Title: "A", Status: StatusPending}, "test")

	path, err := SaveBoard(board, dir)
	if err != nil {
		t.Fatal(err)
	}
	if path == "" {
		t.Fatal("expected non-empty path")
	}

	// Verify file exists
	if _, err := os.Stat(path); os.IsNotExist(err) {
		t.Fatal("expected file to exist")
	}

	// Verify legacy file exists
	legacyPath := filepath.Join(dir, "run_graph.json")
	if _, err := os.Stat(legacyPath); os.IsNotExist(err) {
		t.Fatal("expected legacy file to exist")
	}

	loaded, err := LoadBoard(dir)
	if err != nil {
		t.Fatal(err)
	}
	if loaded == nil {
		t.Fatal("expected board to be loaded")
	}
	if loaded.RunID != "run-1" {
		t.Errorf("run_id = %q, want %q", loaded.RunID, "run-1")
	}
	if len(loaded.Nodes) != 1 {
		t.Errorf("nodes = %d, want 1", len(loaded.Nodes))
	}
	if loaded.Nodes[0].ID != "t1" {
		t.Errorf("node id = %q, want %q", loaded.Nodes[0].ID, "t1")
	}
}

func TestLoadBoardNotFound(t *testing.T) {
	dir := t.TempDir()
	loaded, err := LoadBoard(dir)
	if err != nil {
		t.Fatal(err)
	}
	if loaded != nil {
		t.Error("expected nil for non-existent board")
	}
}

func TestLoadBoardLegacyFallback(t *testing.T) {
	dir := t.TempDir()
	board := NewRunTaskBoard("run-2", "feature_delivery")
	data, _ := json.MarshalIndent(board, "", "  ")
	os.WriteFile(filepath.Join(dir, "run_graph.json"), data, 0644)

	loaded, err := LoadBoard(dir)
	if err != nil {
		t.Fatal(err)
	}
	if loaded == nil {
		t.Fatal("expected board to be loaded from legacy file")
	}
	if loaded.RunID != "run-2" {
		t.Errorf("run_id = %q, want %q", loaded.RunID, "run-2")
	}
}
