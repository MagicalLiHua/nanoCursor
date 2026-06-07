package filetools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWriteFile(t *testing.T) {
	dir := t.TempDir()

	result, err := WriteFile(dir, "new.txt", "hello")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "Successfully") {
		t.Error("expected success message")
	}

	content, _ := os.ReadFile(filepath.Join(dir, "new.txt"))
	if string(content) != "hello" {
		t.Errorf("content = %q, want %q", string(content), "hello")
	}
}

func TestWriteFileExists(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "existing.txt"), []byte("old"), 0644)

	result, _ := WriteFile(dir, "existing.txt", "new")
	if !strings.Contains(result, "已存在") {
		t.Error("expected error for existing file")
	}
}

func TestWriteFileWithOptionsOverwrite(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "existing.txt")
	os.WriteFile(path, []byte("old"), 0644)

	result, err := WriteFileWithOptions(dir, "existing.txt", "new", WriteOptions{Overwrite: true})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "updated") {
		t.Errorf("expected updated message, got %q", result)
	}
	content, _ := os.ReadFile(path)
	if string(content) != "new" {
		t.Errorf("content = %q, want %q", string(content), "new")
	}
}

func TestEditFileExact(t *testing.T) {
	dir := t.TempDir()
	content := "line1\nold_code\nline3"
	os.WriteFile(filepath.Join(dir, "edit.txt"), []byte(content), 0644)

	result, err := EditFile(dir, "edit.txt", "old_code", "new_code")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "精确匹配") {
		t.Error("expected exact match strategy")
	}

	newContent, _ := os.ReadFile(filepath.Join(dir, "edit.txt"))
	if !strings.Contains(string(newContent), "new_code") {
		t.Error("expected new_code in file")
	}
}

func TestEditFileWithOptionsLineRange(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "edit.txt")
	os.WriteFile(path, []byte("line1\nline2\nline3\n"), 0644)

	result, err := EditFileWithOptions(dir, "edit.txt", EditOptions{
		StartLine:    2,
		EndLine:      2,
		NewText:      "replaced",
		MatchMode:    "exact",
		CreateBackup: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !result.Changed || !strings.Contains(result.Strategy, "Line Range") {
		t.Errorf("expected line range edit result, got %+v", result)
	}

	newContent, _ := os.ReadFile(path)
	if string(newContent) != "line1\nreplaced\nline3\n" {
		t.Errorf("content = %q", string(newContent))
	}
	if result.BackupPath == "" {
		t.Error("expected backup path")
	}
}

func TestEditFileWithOptionsExactModeSkipsFuzzy(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "edit.txt"), []byte("line1\nold_code\nline3"), 0644)

	result, err := EditFileWithOptions(dir, "edit.txt", EditOptions{
		SearchBlock:  "old_c0de",
		ReplaceBlock: "new_code",
		MatchMode:    "exact",
		CreateBackup: false,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result.Result, "未能") {
		t.Errorf("expected exact mode not found, got: %s", result.Result)
	}
}

func TestEditFileStripped(t *testing.T) {
	dir := t.TempDir()
	content := "line1\nold_code\nline3"
	os.WriteFile(filepath.Join(dir, "edit.txt"), []byte(content), 0644)

	result, err := EditFile(dir, "edit.txt", "  old_code  ", "new_code")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "去空匹配") {
		t.Errorf("expected stripped match strategy, got: %s", result)
	}
}

func TestEditFileFuzzy(t *testing.T) {
	dir := t.TempDir()

	// Use a 20-line block where 19 of 20 lines match exactly.
	// LCS=18 (search has 19 lines, window has 20, 18 of search lines match).
	// Ratio = 2*18 / (19+20) = 36/39 = 92.3% which exceeds the 90% threshold.
	content := strings.Repeat("same\n", 19) + "end"
	os.WriteFile(filepath.Join(dir, "fuzzy.txt"), []byte(content), 0644)

	// 19 search lines: 18 "same" lines + 1 near-miss "ennd" instead of "end".
	searchBlock := strings.Repeat("same\n", 18) + "ennd"
	result, err := EditFile(dir, "fuzzy.txt", searchBlock, "new_code")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "模糊匹配") {
		t.Errorf("expected fuzzy match strategy, got: %s", result)
	}
}

func TestEditFileDuplicate(t *testing.T) {
	dir := t.TempDir()
	content := "foo\nbar\nfoo"
	os.WriteFile(filepath.Join(dir, "dup.txt"), []byte(content), 0644)

	result, _ := EditFile(dir, "dup.txt", "foo", "baz")
	if !strings.Contains(result, "出现 2 次") {
		t.Error("expected duplicate error")
	}
}

func TestSequenceRatio(t *testing.T) {
	if SequenceRatio("hello", "hello") != 1.0 {
		t.Error("expected 1.0 for identical strings")
	}
	if SequenceRatio("hello", "world") >= 0.9 {
		t.Error("expected low ratio for different strings")
	}
}
