//go:build !windows

package mcp

import (
	"bufio"
	"bytes"
	"context"
	"io"
	"os"
	"os/exec"
	"sync"
	"syscall"
	"time"
)

const defaultMaxStderrChars = 20000

// limitedBuffer is a bytes.Buffer that caps stored data at max bytes
// while tracking total bytes written.
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

func applyProcessGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

func killProcessGroup(cmd *exec.Cmd) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
}

func mergeEnv(extra map[string]string) []string {
	env := os.Environ()
	for key, value := range extra {
		env = append(env, key+"="+value)
	}
	return env
}

// StdioProcess manages a child process communicating over stdin/stdout.
type StdioProcess struct {
	stdin  io.WriteCloser
	stdout io.ReadCloser

	cmd    *exec.Cmd
	start  time.Time
	stderr limitedBuffer

	stderrMu   sync.Mutex
	stderrDone chan struct{}
	waitOnce   sync.Once
	waitErr    error
}

// StartStdioProcess launches a child process with the given command, args,
// working directory, and extra environment variables.
func StartStdioProcess(ctx context.Context, command string, args []string, cwd string, env map[string]string) (*StdioProcess, error) {
	maxStderr := defaultMaxStderrChars

	cmd := exec.CommandContext(ctx, command, args...)
	cmd.Dir = cwd
	cmd.Env = mergeEnv(env)
	applyProcessGroup(cmd)

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}

	process := &StdioProcess{
		stdin:  stdin,
		stdout: stdout,
		cmd:    cmd,
		start:  time.Now(),
		stderr: limitedBuffer{max: maxStderr},

		stderrDone: make(chan struct{}),
	}
	go process.captureStderr(stderr)
	return process, nil
}

// Stdin returns the process's stdin pipe.
func (p *StdioProcess) Stdin() io.WriteCloser {
	return p.stdin
}

// Stdout returns the process's stdout pipe.
func (p *StdioProcess) Stdout() io.Reader {
	return p.stdout
}

// StderrString returns captured stderr content.
func (p *StdioProcess) StderrString() string {
	p.stderrMu.Lock()
	defer p.stderrMu.Unlock()
	return p.stderr.String()
}

// Close kills the process and waits for it to exit.
func (p *StdioProcess) Close() error {
	_ = p.stdin.Close()
	killProcessGroup(p.cmd)
	return p.Wait()
}

// Wait waits for the process to exit.
func (p *StdioProcess) Wait() error {
	p.waitOnce.Do(func() {
		p.waitErr = p.cmd.Wait()
		<-p.stderrDone
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
			p.stderrMu.Unlock()
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
