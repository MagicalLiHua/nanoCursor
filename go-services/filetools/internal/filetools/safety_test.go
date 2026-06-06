package filetools

import (
	"os"
	"path/filepath"
	"testing"
)

func TestGetSafeFilepath(t *testing.T) {
	dir := t.TempDir()

	path, err := GetSafeFilepath(dir, "src/main.py")
	if err != nil {
		t.Fatal(err)
	}
	dirResolved, _ := filepath.EvalSymlinks(dir)
	expected := filepath.Join(dirResolved, "src", "main.py")
	if path != expected {
		t.Errorf("path = %q, want %q", path, expected)
	}

	_, err = GetSafeFilepath(dir, "../../etc/passwd")
	if err == nil {
		t.Error("expected error for directory traversal")
	}

	_, err = GetSafeFilepath(dir, "/etc/passwd")
	if err == nil {
		t.Error("expected error for absolute path outside workspace")
	}
}

func TestGetSafeFilepathSymlink(t *testing.T) {
	dir := t.TempDir()
	outside := filepath.Join(dir, "outside")
	os.Mkdir(outside, 0755)
	os.WriteFile(filepath.Join(outside, "secret.txt"), []byte("secret"), 0644)

	inside := filepath.Join(dir, "inside")
	os.Symlink(outside, inside)

	_, err := GetSafeFilepath(dir, "inside/secret.txt")
	if err == nil {
		t.Error("expected error for symlink pointing outside workspace")
	}
}
