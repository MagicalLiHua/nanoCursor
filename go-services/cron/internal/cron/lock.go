package cron

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// CronLock is a PID file lock to prevent duplicate scheduler instances.
type CronLock struct {
	lockFile string
}

// NewCronLock creates a lock for the given workspace directory.
func NewCronLock(workspaceDir, name string) *CronLock {
	dir := filepath.Join(workspaceDir, ".claude")
	return &CronLock{
		lockFile: filepath.Join(dir, fmt.Sprintf(".cron_lock_%s", name)),
	}
}

// Acquire tries to acquire the lock. Returns true if successful.
func (l *CronLock) Acquire() bool {
	if data, err := os.ReadFile(l.lockFile); err == nil {
		pidStr := strings.TrimSpace(string(data))
		if pid, err := strconv.Atoi(pidStr); err == nil {
			if process, err := os.FindProcess(pid); err == nil {
				if err := process.Signal(os.Interrupt); err == nil {
					return false // process still running
				}
			}
		}
	}
	_ = os.MkdirAll(filepath.Dir(l.lockFile), 0o755)
	_ = os.WriteFile(l.lockFile, []byte(fmt.Sprintf("%d", os.Getpid())), 0o644)
	return true
}

// Release removes the lock file.
func (l *CronLock) Release() {
	_ = os.Remove(l.lockFile)
}
