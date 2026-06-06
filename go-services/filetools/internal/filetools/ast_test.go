package filetools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestExtractOutline(t *testing.T) {
	dir := t.TempDir()
	pyFile := filepath.Join(dir, "sample.py")
	content := `import os

class MyService:
    def process(self):
        pass
    def handle(self):
        pass

def helper():
    return 42
`
	os.WriteFile(pyFile, []byte(content), 0644)

	outline, err := ExtractOutline(pyFile)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(outline, "class MyService") {
		t.Error("expected class MyService in outline")
	}
	if !strings.Contains(outline, "def helper") {
		t.Error("expected def helper in outline")
	}
	t.Logf("outline:\n%s", outline)
}

func TestExtractFunctionSource(t *testing.T) {
	dir := t.TempDir()
	pyFile := filepath.Join(dir, "sample.py")
	content := `def hello():
    return "world"

def goodbye():
    return "farewell"
`
	os.WriteFile(pyFile, []byte(content), 0644)

	source, err := ExtractFunctionSource(pyFile, "hello")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(source, `return "world"`) {
		t.Errorf("expected 'return world' in source")
	}
	if strings.Contains(source, "farewell") {
		t.Error("should not contain farewell")
	}
}

func TestExtractClassSource(t *testing.T) {
	dir := t.TempDir()
	pyFile := filepath.Join(dir, "sample.py")
	content := `class Foo:
    def bar(self):
        pass

class Baz:
    def qux(self):
        pass
`
	os.WriteFile(pyFile, []byte(content), 0644)

	source, err := ExtractClassSource(pyFile, "Foo")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(source, "def bar") {
		t.Error("expected 'def bar' in source")
	}
	if strings.Contains(source, "def qux") {
		t.Error("should not contain 'def qux'")
	}
}

func TestExtractNotFound(t *testing.T) {
	dir := t.TempDir()
	pyFile := filepath.Join(dir, "sample.py")
	os.WriteFile(pyFile, []byte("x = 1\n"), 0644)

	source, _ := ExtractFunctionSource(pyFile, "nonexistent")
	if !strings.Contains(source, "未找到") {
		t.Error("expected 'not found' message")
	}
}
