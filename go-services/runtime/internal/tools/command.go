package tools

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"nanocursor/go-runtime/internal/policy"
	"nanocursor/go-runtime/internal/supervisor"
)

type CommandRequest struct {
	ConversationID string `json:"conversation_id,omitempty"`
	RunID          string `json:"run_id,omitempty"`
	ToolCallID     string `json:"tool_call_id,omitempty"`
	WorkspaceDir   string `json:"workspace_dir"`
	Tool           string `json:"tool"`
	Input          struct {
		Command        string         `json:"command"`
		Cwd            string         `json:"cwd,omitempty"`
		TimeoutMS      int            `json:"timeout_ms,omitempty"`
		MaxStdoutChars int            `json:"max_stdout_chars,omitempty"`
		MaxStderrChars int            `json:"max_stderr_chars,omitempty"`
		Env            map[string]any `json:"env,omitempty"`
	} `json:"input"`
	Policy struct {
		PermissionLevel  string `json:"permission_level,omitempty"`
		RequiresApproval bool   `json:"requires_approval,omitempty"`
		ApprovalID       string `json:"approval_id,omitempty"`
		ApprovalToken    string `json:"approval_token,omitempty"`
	} `json:"policy"`
}

type PreviewRequest struct {
	ConversationID string         `json:"conversation_id,omitempty"`
	RunID          string         `json:"run_id,omitempty"`
	WorkspaceDir   string         `json:"workspace_dir"`
	Tool           string         `json:"tool"`
	Input          map[string]any `json:"input"`
	PythonPolicy   struct {
		PermissionLevel  string `json:"permission_level,omitempty"`
		RequiresApproval bool   `json:"requires_approval,omitempty"`
		ApprovalID       string `json:"approval_id,omitempty"`
		ApprovalToken    string `json:"approval_token,omitempty"`
	} `json:"python_policy"`
}

type Manager struct {
	mu      sync.RWMutex
	runs    map[string]*ToolRun
	cancel  map[string]context.CancelFunc
	limiter *supervisor.Limiter
}

func NewManager() *Manager {
	return newManagerWithLimits(4, 2)
}

func newManagerWithLimits(maxWorkspaceRuns int, maxExternalRunTools int) *Manager {
	if maxWorkspaceRuns <= 0 {
		maxWorkspaceRuns = 1
	}
	if maxExternalRunTools <= 0 {
		maxExternalRunTools = 1
	}
	return &Manager{
		runs:    map[string]*ToolRun{},
		cancel:  map[string]context.CancelFunc{},
		limiter: supervisor.NewLimiter(maxWorkspaceRuns, maxExternalRunTools),
	}
}

func (m *Manager) Preview(req PreviewRequest) policy.Decision {
	return policy.Preview(policy.Input{
		WorkspaceDir:     req.WorkspaceDir,
		Cwd:              stringFromMap(req.Input, "cwd"),
		Command:          stringFromMap(req.Input, "command"),
		PermissionLevel:  req.PythonPolicy.PermissionLevel,
		RequiresApproval: req.PythonPolicy.RequiresApproval,
		ApprovalID:       req.PythonPolicy.ApprovalID,
		ApprovalToken:    req.PythonPolicy.ApprovalToken,
	})
}

func (m *Manager) Execute(req CommandRequest) (*ToolRun, error) {
	command := req.Input.Command
	if command == "" {
		return nil, errors.New("command is required")
	}
	decision := policy.Preview(policy.Input{
		WorkspaceDir:     req.WorkspaceDir,
		Cwd:              req.Input.Cwd,
		Command:          command,
		PermissionLevel:  req.Policy.PermissionLevel,
		RequiresApproval: req.Policy.RequiresApproval,
		ApprovalID:       req.Policy.ApprovalID,
		ApprovalToken:    req.Policy.ApprovalToken,
	})

	runID := newID("tr")
	run := &ToolRun{
		ToolRunID: runID,
		Status:    "running",
		Backend:   "go_runtime",
		Tool:      req.Tool,
		Command:   command,
		Cwd:       decision.Cwd,
		ExitCode:  -1,
		Evidence: ToolEvidence{
			Kind:      "command_result",
			Summary:   "command running",
			Artifacts: []string{},
		},
	}
	if req.Tool == "" {
		run.Tool = "run_command"
	}

	if !decision.Allowed {
		run.Status = "denied"
		run.ErrorCode = decision.ErrorCode
		run.Message = decision.Message
		run.Stderr = decision.Message
		run.Evidence.Summary = decision.Message
		m.store(run)
		m.addEvent(runID, req.RunID, "policy.denied", map[string]any{
			"error_code": decision.ErrorCode,
			"message":    decision.Message,
			"reasons":    decision.Reasons,
		})
		return run, nil
	}

	slot, snapshot, ok := m.limiter.TryAcquire(supervisor.LimitScope{Workspace: decision.Cwd, RunID: req.RunID})
	if !ok {
		run.Status = "failed"
		run.ErrorCode = "runtime_busy"
		run.Message = "Go runtime is busy for this workspace or run"
		run.Stderr = run.Message
		run.Evidence.Summary = run.Message
		m.store(run)
		m.addEvent(runID, req.RunID, "runtime.busy", map[string]any{
			"error_code":             run.ErrorCode,
			"message":                run.Message,
			"workspace_active":       snapshot.WorkspaceActive,
			"run_active":             snapshot.RunActive,
			"max_workspace_runs":     snapshot.MaxWorkspace,
			"max_external_run_tools": snapshot.MaxRun,
		})
		return run, nil
	}

	m.store(run)
	timeout := req.Input.TimeoutMS
	if timeout <= 0 {
		timeout = 120000
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Millisecond)
	m.mu.Lock()
	m.cancel[runID] = cancel
	m.mu.Unlock()

	go m.runCommand(ctx, cancel, slot, runID, req.RunID, command, decision.Cwd, req)
	return run, nil
}

func (m *Manager) Get(id string) (*ToolRun, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	run, ok := m.runs[id]
	if !ok {
		return nil, false
	}
	copyRun := *run
	copyRun.events = append([]ToolEvent{}, run.events...)
	return &copyRun, true
}

func (m *Manager) Events(id string) ([]ToolEvent, bool) {
	return m.EventsAfter(id, 0)
}

func (m *Manager) EventsAfter(id string, after int) ([]ToolEvent, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	run, ok := m.runs[id]
	if !ok {
		return nil, false
	}
	if after < 0 {
		after = 0
	}
	if after > len(run.events) {
		after = len(run.events)
	}
	return append([]ToolEvent{}, run.events[after:]...), true
}

func (m *Manager) Cancel(id string) bool {
	m.mu.Lock()
	cancel, ok := m.cancel[id]
	m.mu.Unlock()
	if !ok {
		return false
	}
	cancel()
	return true
}

func (m *Manager) runCommand(ctx context.Context, cancel context.CancelFunc, slot *supervisor.LimitSlot, runID string, externalRunID string, command string, cwd string, req CommandRequest) {
	defer cancel()
	defer func() {
		m.mu.Lock()
		delete(m.cancel, runID)
		m.mu.Unlock()
		slot.Release()
	}()

	result := supervisor.RunCommand(
		ctx,
		supervisor.ProcessSpec{
			Kind:           req.Tool,
			Command:        command,
			Cwd:            cwd,
			TimeoutMS:      req.Input.TimeoutMS,
			MaxStdoutChars: req.Input.MaxStdoutChars,
			MaxStderrChars: req.Input.MaxStderrChars,
		},
		func(event supervisor.ProcessEvent) {
			m.addEvent(runID, externalRunID, event.Type, event.Payload)
		},
	)

	m.mu.Lock()
	run := m.runs[runID]
	run.Status = result.Status
	run.ExitCode = result.ExitCode
	run.Stdout = result.Stdout
	run.Stderr = result.Stderr
	run.StdoutTruncated = result.StdoutTruncated
	run.StderrTruncated = result.StderrTruncated
	run.DurationMS = result.DurationMS
	run.TimedOut = result.TimedOut
	if result.Status == "completed" {
		run.Evidence.Summary = "command completed"
	} else {
		run.Evidence.Summary = fmt.Sprintf("command %s", result.Status)
	}
	m.mu.Unlock()
}

func (m *Manager) store(run *ToolRun) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.runs[run.ToolRunID] = run
}

func (m *Manager) addEvent(runID string, externalRunID string, eventType string, payload map[string]any) {
	m.mu.Lock()
	defer m.mu.Unlock()
	run := m.runs[runID]
	if run == nil {
		return
	}
	event := ToolEvent{
		ID:        newID("evt"),
		Ts:        time.Now().UTC(),
		RunID:     externalRunID,
		ToolRunID: runID,
		Type:      eventType,
		Payload:   payload,
	}
	run.events = append(run.events, event)
}

func stringFromMap(input map[string]any, key string) string {
	value, _ := input[key].(string)
	return value
}

func newID(prefix string) string {
	return fmt.Sprintf("%s_%d", prefix, time.Now().UnixNano())
}
