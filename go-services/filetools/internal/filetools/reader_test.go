package filetools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadFile(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "small.txt"), []byte("hello world"), 0644)

	result, err := ReadFile(dir, "small.txt")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "hello world") {
		t.Error("expected content in result")
	}
}

func TestReadFileLarge(t *testing.T) {
	dir := t.TempDir()
	content := strings.Repeat("# comment\n", 600)
	os.WriteFile(filepath.Join(dir, "large.py"), []byte(content), 0644)

	result, err := ReadFile(dir, "large.py")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "大文件") {
		t.Error("expected '大文件' in result for large file")
	}
}

func TestReadFunction(t *testing.T) {
	dir := t.TempDir()
	content := `def hello():
    return "world"

def goodbye():
    return "farewell"
`
	os.WriteFile(filepath.Join(dir, "sample.py"), []byte(content), 0644)

	result, err := ReadFunction(dir, "sample.py", "hello")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, `return "world"`) {
		t.Error("expected function source")
	}
}

func TestReadClass(t *testing.T) {
	dir := t.TempDir()
	content := `class Foo:
    def bar(self):
        pass
`
	os.WriteFile(filepath.Join(dir, "sample.py"), []byte(content), 0644)

	result, err := ReadClass(dir, "sample.py", "Foo")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "def bar") {
		t.Error("expected class source")
	}
}

func TestReadFileRange(t *testing.T) {
	dir := t.TempDir()
	content := "line1\nline2\nline3\nline4\nline5"
	os.WriteFile(filepath.Join(dir, "lines.txt"), []byte(content), 0644)

	result, err := ReadFileRange(dir, "lines.txt", 2, 4)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "line2") || !strings.Contains(result, "line4") {
		t.Error("expected lines 2-4")
	}
}

func TestListDirectory(t *testing.T) {
	dir := t.TempDir()
	os.Mkdir(filepath.Join(dir, "subdir"), 0755)
	os.Mkdir(filepath.Join(dir, "__pycache__"), 0755)
	os.WriteFile(filepath.Join(dir, "file.txt"), []byte(""), 0644)
	os.WriteFile(filepath.Join(dir, "module.pyc"), []byte(""), 0644)

	result, err := ListDirectory(dir, ".")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "[DIR]  subdir") {
		t.Error("expected subdir in listing")
	}
	if !strings.Contains(result, "[FILE] file.txt") {
		t.Error("expected file.txt in listing")
	}
	if strings.Contains(result, "__pycache__") || strings.Contains(result, "module.pyc") {
		t.Errorf("expected hidden entries to be filtered, got: %s", result)
	}
}

func TestReadFileNotFound(t *testing.T) {
	dir := t.TempDir()
	result, err := ReadFile(dir, "nonexistent.txt")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "does not exist") {
		t.Error("expected 'does not exist' message")
	}
}
