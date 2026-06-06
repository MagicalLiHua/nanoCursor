package indexer

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func setupTestWorkspace(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()

	// Create a Python source file
	pyDir := filepath.Join(dir, "src")
	os.MkdirAll(pyDir, 0755)
	os.WriteFile(filepath.Join(pyDir, "main.py"), []byte(`import os
from fastapi import FastAPI

app = FastAPI()

class UserService:
    def get_user(self, id):
        return {"id": id}

@app.get("/api/users")
def list_users():
    return UserService().get_user(1)
`), 0644)

	// Create a JS file
	jsDir := filepath.Join(dir, "frontend")
	os.MkdirAll(jsDir, 0755)
	os.WriteFile(filepath.Join(jsDir, "app.js"), []byte(`import React from 'react';

function App() {
    return <div>Hello</div>;
}

export default App;
`), 0644)

	// Create a test file
	testDir := filepath.Join(dir, "tests")
	os.MkdirAll(testDir, 0755)
	os.WriteFile(filepath.Join(testDir, "test_main.py"), []byte(`def test_hello():
    assert True
`), 0644)

	// Create a config file
	os.WriteFile(filepath.Join(dir, "config.yaml"), []byte(`port: 8080
`), 0644)

	// Create a README
	os.WriteFile(filepath.Join(dir, "README.md"), []byte(`# Test Project
`), 0644)

	return dir
}

func TestBuild(t *testing.T) {
	dir := setupTestWorkspace(t)
	idx := NewProjectIndex(dir)

	built, count, err := idx.Build(true)
	if err != nil {
		t.Fatalf("Build failed: %v", err)
	}
	if !built {
		t.Error("expected built=true")
	}
	if count < 4 {
		t.Errorf("expected at least 4 files, got %d", count)
	}

	if _, err := os.Stat(idx.IndexPath()); os.IsNotExist(err) {
		t.Error("expected index file to be created")
	}

	t.Logf("indexed %d files", count)
}

func TestBuildIdempotent(t *testing.T) {
	dir := setupTestWorkspace(t)
	idx := NewProjectIndex(dir)

	idx.Build(true)
	built, count, _ := idx.Build(false)
	if built {
		t.Error("expected built=false on second call without force")
	}
	if count < 4 {
		t.Errorf("expected at least 4 files, got %d", count)
	}
}

func TestUpdate(t *testing.T) {
	dir := setupTestWorkspace(t)
	idx := NewProjectIndex(dir)

	idx.Build(true)

	// Add a new file
	os.WriteFile(filepath.Join(dir, "src", "utils.py"), []byte(`def helper():
    return 42
`), 0644)

	updated, removed, err := idx.Update()
	if err != nil {
		t.Fatalf("Update failed: %v", err)
	}
	if updated < 1 {
		t.Errorf("expected at least 1 updated file, got %d", updated)
	}
	if removed != 0 {
		t.Errorf("expected 0 removed, got %d", removed)
	}
}

func TestSearchSymbol(t *testing.T) {
	dir := setupTestWorkspace(t)
	idx := NewProjectIndex(dir)
	idx.Build(true)

	results := idx.SearchSymbol("UserService")
	if len(results) == 0 {
		t.Fatal("expected to find UserService")
	}
	found := false
	for _, r := range results {
		if r.SymbolName == "UserService" {
			found = true
			t.Logf("found: %+v", r)
		}
	}
	if !found {
		t.Error("expected to find UserService in results")
	}
}

func TestSearchDependents(t *testing.T) {
	dir := setupTestWorkspace(t)
	idx := NewProjectIndex(dir)
	idx.Build(true)

	deps := idx.SearchDependents("fastapi")
	if len(deps) == 0 {
		t.Error("expected to find files importing fastapi")
	}
	t.Logf("dependents of fastapi: %v", deps)
}

func TestSummary(t *testing.T) {
	dir := setupTestWorkspace(t)
	idx := NewProjectIndex(dir)
	idx.Build(true)

	s := idx.Summary()
	if s.TotalFiles < 4 {
		t.Errorf("expected at least 4 total files, got %d", s.TotalFiles)
	}
	if s.SourceCount < 1 {
		t.Errorf("expected at least 1 source file, got %d", s.SourceCount)
	}
	if s.TestCount < 1 {
		t.Errorf("expected at least 1 test file, got %d", s.TestCount)
	}
	if s.SummaryText == "" {
		t.Error("expected non-empty summary text")
	}
	t.Logf("summary:\n%s", s.SummaryText)
}

func TestRouteSummary(t *testing.T) {
	dir := setupTestWorkspace(t)
	idx := NewProjectIndex(dir)
	idx.Build(true)

	routes := idx.RouteSummary()
	if len(routes) == 0 {
		t.Error("expected to find at least 1 route")
	}
	for _, r := range routes {
		t.Logf("route: %s %s -> %s (%s:%d)", r.Method, r.Path, r.Handler, r.File, r.LineNo)
	}
}

func TestCallers(t *testing.T) {
	dir := setupTestWorkspace(t)
	idx := NewProjectIndex(dir)
	idx.Build(true)

	callers := idx.Callers("UserService")
	t.Logf("callers of UserService: %v", callers)
}

func TestBuildAndLoad(t *testing.T) {
	dir := setupTestWorkspace(t)
	idx := NewProjectIndex(dir)
	idx.Build(true)

	idx2 := NewProjectIndex(dir)
	if err := idx2.load(); err != nil {
		t.Fatalf("load failed: %v", err)
	}

	if len(idx2.entries) != len(idx.entries) {
		t.Errorf("loaded entries count %d != original %d", len(idx2.entries), len(idx.entries))
	}
}

// BenchmarkBuild measures full index build performance.
func BenchmarkBuild(b *testing.B) {
	dir, err := os.MkdirTemp("", "bench-*")
	if err != nil {
		b.Fatal(err)
	}
	defer os.RemoveAll(dir)

	pyDir := filepath.Join(dir, "src")
	os.MkdirAll(pyDir, 0755)
	for i := 0; i < 50; i++ {
		fname := filepath.Join(pyDir, "file"+fmt.Sprintf("%d", i)+".py")
		os.WriteFile(fname, []byte(`import os
class Foo:
    def bar(self):
        pass
def baz():
    return Foo().bar()
`), 0644)
	}

	idx := NewProjectIndex(dir)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		idx.Build(true)
	}
}
