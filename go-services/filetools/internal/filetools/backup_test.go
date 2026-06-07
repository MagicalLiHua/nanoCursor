package filetools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBackupAndRollback(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "data.txt"), []byte("original"), 0644)

	backupPath := BackupFile(dir, "data.txt")
	if backupPath == "" {
		t.Fatal("expected backup path")
	}

	// Modify file
	os.WriteFile(filepath.Join(dir, "data.txt"), []byte("modified"), 0644)

	// Rollback
	result, err := RollbackFile(dir, "data.txt", -1)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "成功回滚") {
		t.Errorf("expected success message: %s", result)
	}

	content, _ := os.ReadFile(filepath.Join(dir, "data.txt"))
	if string(content) != "original" {
		t.Errorf("content = %q, want %q", string(content), "original")
	}
}

func TestListBackups(t *testing.T) {
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, ".backups"), 0755)
	os.WriteFile(filepath.Join(dir, ".backups", "data.txt.bak.20060102_150405"), []byte("backup"), 0644)

	result, err := ListBackups(dir, "data.txt")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "data.txt.bak") {
		t.Error("expected backup in listing")
	}
}

func TestBackupFileNamesDoNotCollide(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "data.txt"), []byte("first"), 0644)

	first := BackupFile(dir, "data.txt")
	second := BackupFile(dir, "data.txt")
	if first == "" || second == "" {
		t.Fatal("expected backup paths")
	}
	if first == second {
		t.Fatalf("expected unique backup names, got %s", first)
	}
}

func TestBackupNonExistent(t *testing.T) {
	dir := t.TempDir()
	result := BackupFile(dir, "nonexistent.txt")
	if result != "" {
		t.Error("expected empty string for non-existent file")
	}
}

func TestRollbackNoBackups(t *testing.T) {
	dir := t.TempDir()
	result, err := RollbackFile(dir, "data.txt", -1)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result, "未找到") {
		t.Error("expected 'not found' message")
	}
}
