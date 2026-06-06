package indexer

import (
	"os"
	"path/filepath"
	"testing"
)

func TestParsePythonFile(t *testing.T) {
	dir := t.TempDir()
	pyFile := filepath.Join(dir, "sample.py")
	content := `import os
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()

class MyService:
    def process(self):
        pass

def handle_request():
    return MyService().process()

@router.get("/api/health")
def health():
    return {"ok": True}

@router.post("/api/items")
def create_item():
    return handle_request()
`
	if err := os.WriteFile(pyFile, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	symbols, imports, routes, callGraph := parsePythonFile(pyFile)

	if len(symbols) < 3 {
		t.Errorf("expected at least 3 symbols, got %d", len(symbols))
	}

	foundClass := false
	for _, s := range symbols {
		if s.Name == "MyService" && s.Type == "class" {
			foundClass = true
		}
	}
	if !foundClass {
		t.Error("expected to find class MyService")
	}

	if len(imports) < 2 {
		t.Errorf("expected at least 2 imports, got %d: %v", len(imports), imports)
	}

	if len(routes) < 2 {
		t.Errorf("expected at least 2 routes, got %d", len(routes))
	}
	for _, r := range routes {
		if r.Method == "" || r.Path == "" {
			t.Errorf("route missing method or path: %+v", r)
		}
	}

	if len(callGraph) == 0 {
		t.Error("expected non-empty call graph")
	}

	t.Logf("symbols: %+v", symbols)
	t.Logf("imports: %+v", imports)
	t.Logf("routes: %+v", routes)
	t.Logf("callGraph: %+v", callGraph)
}

func TestParsePythonFileSyntaxError(t *testing.T) {
	dir := t.TempDir()
	pyFile := filepath.Join(dir, "broken.py")
	content := `def foo(:
    pass
`
	if err := os.WriteFile(pyFile, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	// Should not panic, just return empty results
	symbols, imports, routes, _ := parsePythonFile(pyFile)
	t.Logf("broken file: symbols=%d imports=%d routes=%d", len(symbols), len(imports), len(routes))
}
