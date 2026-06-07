package executor

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"io"
	"os/exec"
	"runtime"
	"sync"
	"time"
)

const (
	defaultTimeoutMS      = 120000
	defaultMaxStdoutChars = 100000
	defaultMaxStderrChars = 20000
)

func RunCommand(ctx context.Context, spec ProcessSpec, emit EventSink) ProcessResult {
	start := time.Now()
	if spec.TimeoutMS <= 0 {
		spec.TimeoutMS = defaultTimeoutMS
	}
	if spec.MaxStdoutChars <= 0 {
		spec.MaxStdoutChars = defaultMaxStdoutChars
	}
	if spec.MaxStderrChars <= 0 {
		spec.MaxStderrChars = defaultMaxStderrChars
	}

	emitEvent(emit, "tool.started", map[string]any{
		"kind":    spec.Kind,
		"command": spec.Command,
		"cwd":     spec.Cwd,
	})

	cmd := shellCommand(ctx, spec.Command)
	cmd.Dir = spec.Cwd
	applyProcessGroup(cmd)

	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return finishError(emit, "failed", err, start)
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		return finishError(emit, "failed", err, start)
	}
	if err := cmd.Start(); err != nil {
		return finishError(emit, "failed", err, start)
	}

	var stdout limitedBuffer
	var stderr limitedBuffer
	stdout.max = spec.MaxStdoutChars
	stderr.max = spec.MaxStderrChars

	var wg sync.WaitGroup
	wg.Add(2)
	go capturePipe(&wg, emit, "tool.stdout", stdoutPipe, &stdout)
	go capturePipe(&wg, emit, "tool.stderr", stderrPipe, &stderr)

	err = cmd.Wait()
	wg.Wait()
	timedOut := ctx.Err() == context.DeadlineExceeded
	if ctx.Err() != nil {
		killProcessGroup(cmd)
	}

	exitCode := 0
	status := "completed"
	if err != nil {
		status = "failed"
		exitCode = -1
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			exitCode = exitErr.ExitCode()
		}
	}
	if timedOut {
		status = "timeout"
		exitCode = -1
	}
	if ctx.Err() == context.Canceled && !timedOut {
		status = "cancelled"
		exitCode = -1
	}

	result := ProcessResult{
		Status:          status,
		ExitCode:        exitCode,
		Stdout:          stdout.String(),
		Stderr:          stderr.String(),
		StdoutTruncated: stdout.truncated,
		StderrTruncated: stderr.truncated,
		StdoutBytes:     stdout.totalBytes,
		StderrBytes:     stderr.totalBytes,
		DurationMS:      time.Since(start).Milliseconds(),
		TimedOut:        timedOut,
	}
	emitEvent(emit, "tool."+status, map[string]any{
		"exit_code":            exitCode,
		"timed_out":            timedOut,
		"duration_ms":          result.DurationMS,
		"stdout_bytes":         result.StdoutBytes,
		"stderr_bytes":         result.StderrBytes,
		"stdout_truncated":     result.StdoutTruncated,
		"stderr_truncated":     result.StderrTruncated,
		"supervisor_status":    status,
		"supervisor_exit_code": exitCode,
	})
	return result
}

func capturePipe(wg *sync.WaitGroup, emit EventSink, eventType string, pipe io.Reader, out *limitedBuffer) {
	defer wg.Done()
	reader := bufio.NewReader(pipe)
	for {
		chunk, err := reader.ReadBytes('\n')
		if len(chunk) > 0 {
			out.Write(chunk)
			emitEvent(emit, eventType, map[string]any{
				"text":      string(chunk),
				"bytes":     len(chunk),
				"truncated": out.truncated,
			})
		}
		if err != nil {
			return
		}
	}
}

func finishError(emit EventSink, status string, err error, start time.Time) ProcessResult {
	result := ProcessResult{
		Status:     status,
		ExitCode:   -1,
		Stderr:     err.Error(),
		DurationMS: time.Since(start).Milliseconds(),
		Error:      err.Error(),
	}
	emitEvent(emit, "tool."+status, map[string]any{"error": err.Error(), "duration_ms": result.DurationMS})
	return result
}

func emitEvent(emit EventSink, eventType string, payload map[string]any) {
	if emit == nil {
		return
	}
	emit(ProcessEvent{Type: eventType, Payload: payload})
}

type limitedBuffer struct {
	bytes.Buffer
	max        int
	truncated  bool
	totalBytes int
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	b.totalBytes += len(p)
	if b.max <= 0 {
		return len(p), nil
	}
	remaining := b.max - b.Len()
	if remaining <= 0 {
		b.truncated = true
		return len(p), nil
	}
	if len(p) > remaining {
		b.truncated = true
		_, _ = b.Buffer.Write(p[:remaining])
		return len(p), nil
	}
	return b.Buffer.Write(p)
}

func shellCommand(ctx context.Context, command string) *exec.Cmd {
	if runtime.GOOS == "windows" {
		return exec.CommandContext(ctx, "cmd", "/C", command)
	}
	return exec.CommandContext(ctx, "sh", "-c", command)
}
