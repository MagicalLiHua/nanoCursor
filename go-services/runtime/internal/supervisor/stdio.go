package supervisor

import (
	"bufio"
	"context"
	"io"
	"os"
	"os/exec"
	"sync"
	"time"
)

type StdioProcess struct {
	stdin  io.WriteCloser
	stdout io.ReadCloser

	cmd    *exec.Cmd
	emit   EventSink
	start  time.Time
	stderr limitedBuffer

	stderrMu   sync.Mutex
	stderrDone chan struct{}
	waitOnce   sync.Once
	waitErr    error
}

func StartStdioProcess(ctx context.Context, spec StdioProcessSpec, emit EventSink) (*StdioProcess, error) {
	start := time.Now()
	if spec.MaxStderrChars <= 0 {
		spec.MaxStderrChars = defaultMaxStderrChars
	}
	emitEvent(emit, "tool.started", map[string]any{
		"kind":    spec.Kind,
		"command": spec.Command,
		"args":    spec.Args,
		"cwd":     spec.Cwd,
	})

	cmd := exec.CommandContext(ctx, spec.Command, spec.Args...)
	cmd.Dir = spec.Cwd
	cmd.Env = mergeEnv(spec.Env)
	applyProcessGroup(cmd)

	stdin, err := cmd.StdinPipe()
	if err != nil {
		emitEvent(emit, "tool.failed", map[string]any{"error": err.Error(), "duration_ms": time.Since(start).Milliseconds()})
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		emitEvent(emit, "tool.failed", map[string]any{"error": err.Error(), "duration_ms": time.Since(start).Milliseconds()})
		return nil, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		emitEvent(emit, "tool.failed", map[string]any{"error": err.Error(), "duration_ms": time.Since(start).Milliseconds()})
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		emitEvent(emit, "tool.failed", map[string]any{"error": err.Error(), "duration_ms": time.Since(start).Milliseconds()})
		return nil, err
	}

	process := &StdioProcess{
		stdin:  stdin,
		stdout: stdout,
		cmd:    cmd,
		emit:   emit,
		start:  start,
		stderr: limitedBuffer{max: spec.MaxStderrChars},

		stderrDone: make(chan struct{}),
	}
	go process.captureStderr(stderr)
	return process, nil
}

func (p *StdioProcess) Stdin() io.WriteCloser {
	return p.stdin
}

func (p *StdioProcess) Stdout() io.Reader {
	return p.stdout
}

func (p *StdioProcess) StderrString() string {
	p.stderrMu.Lock()
	defer p.stderrMu.Unlock()
	return p.stderr.String()
}

func (p *StdioProcess) Close() error {
	_ = p.stdin.Close()
	killProcessGroup(p.cmd)
	return p.Wait()
}

func (p *StdioProcess) Wait() error {
	p.waitOnce.Do(func() {
		p.waitErr = p.cmd.Wait()
		<-p.stderrDone
		status := "completed"
		exitCode := 0
		if p.waitErr != nil {
			status = "failed"
			exitCode = -1
			if exitErr, ok := p.waitErr.(*exec.ExitError); ok {
				exitCode = exitErr.ExitCode()
			}
		}
		emitEvent(p.emit, "tool."+status, map[string]any{
			"exit_code":          exitCode,
			"duration_ms":        time.Since(p.start).Milliseconds(),
			"stderr_bytes":       p.stderrBytes(),
			"stderr_truncated":   p.stderrTruncated(),
			"supervisor_status":  status,
			"supervisor_process": "stdio",
		})
	})
	return p.waitErr
}

func (p *StdioProcess) captureStderr(stderr io.Reader) {
	defer close(p.stderrDone)
	reader := bufio.NewReader(stderr)
	for {
		chunk, err := reader.ReadBytes('\n')
		if len(chunk) > 0 {
			p.stderrMu.Lock()
			p.stderr.Write(chunk)
			truncated := p.stderr.truncated
			p.stderrMu.Unlock()
			emitEvent(p.emit, "tool.stderr", map[string]any{
				"text":      string(chunk),
				"bytes":     len(chunk),
				"truncated": truncated,
			})
		}
		if err != nil {
			return
		}
	}
}

func (p *StdioProcess) stderrBytes() int {
	p.stderrMu.Lock()
	defer p.stderrMu.Unlock()
	return p.stderr.totalBytes
}

func (p *StdioProcess) stderrTruncated() bool {
	p.stderrMu.Lock()
	defer p.stderrMu.Unlock()
	return p.stderr.truncated
}

func mergeEnv(extra map[string]string) []string {
	env := os.Environ()
	for key, value := range extra {
		env = append(env, key+"="+value)
	}
	return env
}
