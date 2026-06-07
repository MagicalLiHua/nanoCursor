package cron

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCronLockAcquireRelease(t *testing.T) {
	dir := t.TempDir()
	lock := NewCronLock(dir, "test")
	if !lock.Acquire() {
		t.Fatal("first acquire should succeed")
	}
	lock.Release()
	if !lock.Acquire() {
		t.Fatal("acquire after release should succeed")
	}
	lock.Release()
}

func TestCronLockStalePID(t *testing.T) {
	dir := t.TempDir()
	lockFile := filepath.Join(dir, ".claude", ".cron_lock_stale")
	os.MkdirAll(filepath.Dir(lockFile), 0o755)
	os.WriteFile(lockFile, []byte("99999999"), 0o644)

	lock := NewCronLock(dir, "stale")
	if !lock.Acquire() {
		t.Fatal("acquire should succeed for stale PID")
	}
	lock.Release()
}
