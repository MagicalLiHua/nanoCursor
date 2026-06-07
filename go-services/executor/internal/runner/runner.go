package runner

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"sync"
	"time"

	policyclient "nanocursor/go-services/policy/client"

	"nanocursor/go-services/executor/internal/executor"
)

const (
	defaultTimeoutMS = 120000
)

// RunEvent is a serializable event for gRPC streaming.
type RunEvent struct {
	Seq       int32
	Type      string
	Timestamp int64
	RunID     string
	Data      string
}

// RunState tracks a single command execution.
type RunState struct {
	ID              string
	Status          string
	Command         string
	Cwd             string
	ExitCode        int
	Stdout          string
	Stderr          string
	DurationMS      int64
	TimedOut        bool
	ErrorCode       string
	Message         string
	StdoutTruncated bool
	StderrTruncated bool
	events          []RunEvent
}

// RunManager manages command executions.
type RunManager struct {
	mu         sync.RWMutex
	runs       map[string]*RunState
	cancel     map[string]context.CancelFunc
	limiter    *executor.Limiter
	policyAddr string
}

func NewRunManager() *RunManager {
	return NewRunManagerWithLimits(4, 2)
}

func NewRunManagerWithLimits(maxWorkspace, maxRun int) *RunManager {
	addr := "localhost:50052"
	return &RunManager{
		runs:       map[string]*RunState{},
		cancel:     map[string]context.CancelFunc{},
		limiter:    executor.NewLimiter(maxWorkspace, maxRun),
		policyAddr: addr,
	}
}

// Preview calls go-policy to classify the command, then checks workspace boundary and approval token.
func (m *RunManager) Preview(command, cwd, workspaceDir, permissionLevel string, requiresApproval bool, approvalID, approvalToken string) (bool, string, bool, string, string, []string, string, string) {
	workspace, resolvedCwd, reason, ok := normalizeWorkspace(workspaceDir, cwd)
	if !ok {
		return false, normalizedPermission(command, permissionLevel), requiresApproval, "workspace_boundary_violation", reason, []string{reason}, "", ""
	}

	classification := classifyViaPolicy(m.policyAddr, command)
	level := classification
	if permissionLevel != "" {
		level = permissionLevel
	}
	reasons := []string{"cwd is inside workspace"}

	if classification == "shell_risky" {
		reasons = append(reasons, "matched risky command pattern")
		if approvalToken == "" {
			return false, "shell_risky", true, "approval_required", "shell_risky command requires approval token", reasons, workspace, resolvedCwd
		}
		input := executor.TokenInput{
			Command:          command,
			WorkspaceDir:     workspace,
			PermissionLevel:  level,
			RequiresApproval: requiresApproval,
			ApprovalID:       approvalID,
			ApprovalToken:    approvalToken,
		}
		if msg := executor.ValidateApprovalToken(input, workspace); msg != "" {
			return false, "shell_risky", true, "approval_invalid", msg, append(reasons, msg), workspace, resolvedCwd
		}
		level = "shell_risky"
	}

	if requiresApproval && approvalToken == "" {
		return false, level, true, "approval_required", "approved tool call is missing approval token", append(reasons, "approval token missing"), workspace, resolvedCwd
	}
	if requiresApproval && approvalToken != "" {
		input := executor.TokenInput{
			Command:          command,
			WorkspaceDir:     workspace,
			PermissionLevel:  level,
			RequiresApproval: requiresApproval,
			ApprovalID:       approvalID,
			ApprovalToken:    approvalToken,
		}
		if msg := executor.ValidateApprovalToken(input, workspace); msg != "" {
			return false, level, true, "approval_invalid", msg, append(reasons, msg), workspace, resolvedCwd
		}
	}

	reasons = append(reasons, "command accepted by runtime policy")
	return true, level, requiresApproval || level == "shell_risky", "", "", reasons, workspace, resolvedCwd
}

// Execute starts a command execution asynchronously.
func (m *RunManager) Execute(command, cwd, workspaceDir string, timeoutMS int, runID, approvalToken, permissionLevel string, requiresApproval bool, approvalID string, maxStdout, maxStderr int) (*RunState, error) {
	if command == "" {
		return nil, errors.New("command is required")
	}

	allowed, _, _, errorCode, msg, _, workspace, resolvedCwd := m.Preview(command, cwd, workspaceDir, permissionLevel, requiresApproval, approvalID, approvalToken)

	stateID := newID("tr")
	state := &RunState{
		ID:       stateID,
		Status:   "running",
		Command:  command,
		Cwd:      resolvedCwd,
		ExitCode: -1,
	}

	if !allowed {
		state.Status = "denied"
		state.ErrorCode = errorCode
		state.Message = msg
		state.Stderr = msg
		m.store(state)
		m.addEvent(stateID, runID, "policy.denied", fmt.Sprintf(`{"error_code":"%s","message":"%s"}`, errorCode, msg))
		return state, nil
	}

	slot, snapshot, ok := m.limiter.TryAcquire(executor.LimitScope{Workspace: workspace, RunID: runID})
	if !ok {
		state.Status = "failed"
		state.ErrorCode = "runtime_busy"
		state.Message = "Go runtime is busy for this workspace or run"
		state.Stderr = state.Message
		m.store(state)
		m.addEvent(stateID, runID, "runtime.busy", fmt.Sprintf(`{"workspace_active":%d,"run_active":%d}`, snapshot.WorkspaceActive, snapshot.RunActive))
		return state, nil
	}

	m.store(state)
	if timeoutMS <= 0 {
		timeoutMS = defaultTimeoutMS
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutMS)*time.Millisecond)
	m.mu.Lock()
	m.cancel[stateID] = cancel
	m.mu.Unlock()

	go m.runCommand(ctx, cancel, slot, stateID, runID, command, resolvedCwd, timeoutMS, maxStdout, maxStderr)
	return state, nil
}

// Get returns a copy of the run state.
func (m *RunManager) Get(id string) (*RunState, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	state, ok := m.runs[id]
	if !ok {
		return nil, false
	}
	cp := *state
	cp.events = append([]RunEvent{}, state.events...)
	return &cp, true
}

// EventsAfter returns events after the given cursor position.
func (m *RunManager) EventsAfter(id string, after int) ([]RunEvent, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	state, ok := m.runs[id]
	if !ok {
		return nil, false
	}
	if after < 0 {
		after = 0
	}
	if after > len(state.events) {
		after = len(state.events)
	}
	return append([]RunEvent{}, state.events[after:]...), true
}

// Cancel stops a running command.
func (m *RunManager) Cancel(id string) bool {
	m.mu.Lock()
	cf, ok := m.cancel[id]
	m.mu.Unlock()
	if !ok {
		return false
	}
	cf()
	return true
}

func (m *RunManager) runCommand(ctx context.Context, cancel context.CancelFunc, slot *executor.LimitSlot, stateID, externalRunID, command, cwd string, timeoutMS, maxStdout, maxStderr int) {
	defer cancel()
	defer func() {
		m.mu.Lock()
		delete(m.cancel, stateID)
		m.mu.Unlock()
		slot.Release()
	}()

	result := executor.RunCommand(
		ctx,
		executor.ProcessSpec{
			Kind:           "run_command",
			Command:        command,
			Cwd:            cwd,
			TimeoutMS:      timeoutMS,
			MaxStdoutChars: maxStdout,
			MaxStderrChars: maxStderr,
		},
		func(event executor.ProcessEvent) {
			m.addEvent(stateID, externalRunID, event.Type, fmt.Sprintf("%v", event.Payload))
		},
	)

	m.mu.Lock()
	state := m.runs[stateID]
	state.Status = result.Status
	state.ExitCode = result.ExitCode
	state.Stdout = result.Stdout
	state.Stderr = result.Stderr
	state.StdoutTruncated = result.StdoutTruncated
	state.StderrTruncated = result.StderrTruncated
	state.DurationMS = result.DurationMS
	state.TimedOut = result.TimedOut
	m.mu.Unlock()
}

func (m *RunManager) store(state *RunState) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.runs[state.ID] = state
}

func (m *RunManager) addEvent(stateID, externalRunID, eventType, data string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	state := m.runs[stateID]
	if state == nil {
		return
	}
	state.events = append(state.events, RunEvent{
		Seq:       int32(len(state.events)),
		Type:      eventType,
		Timestamp: time.Now().UnixMilli(),
		RunID:     externalRunID,
		Data:      data,
	})
}

func classifyViaPolicy(policyAddr, command string) string {
	if policyAddr == "" {
		policyAddr = "localhost:50052"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	result, err := policyclient.CheckAction(ctx, policyAddr, command)
	if err != nil {
		return fallbackClassify(command)
	}
	return result.RiskLevel
}

func fallbackClassify(command string) string {
	lower := strings.ToLower(command)
	for _, pattern := range []string{"rm -rf", "sudo ", "shutdown", "reboot", "mkfs", "dd if=", "format c:", "del /f /s", "git reset", "git clean", "git checkout", "curl ", "wget "} {
		if strings.Contains(lower, pattern) {
			return "shell_risky"
		}
	}
	return "shell_safe"
}

func normalizedPermission(command, level string) string {
	if level != "" {
		return level
	}
	if fallbackClassify(command) == "shell_risky" {
		return "shell_risky"
	}
	return "shell_safe"
}

func normalizeWorkspace(workspaceDir, cwd string) (string, string, string, bool) {
	workspace, err := filepath.Abs(workspaceDir)
	if err != nil || workspace == "" {
		return "", "", "workspace_dir is invalid", false
	}
	if cwd == "" {
		cwd = workspace
	}
	resolvedCwd, err := filepath.Abs(cwd)
	if err != nil || resolvedCwd == "" {
		return workspace, "", "cwd is invalid", false
	}
	rel, err := filepath.Rel(workspace, resolvedCwd)
	if err != nil {
		return workspace, resolvedCwd, "cwd cannot be related to workspace", false
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return workspace, resolvedCwd, "cwd is outside workspace", false
	}
	return workspace, resolvedCwd, "", true
}

func newID(prefix string) string {
	return fmt.Sprintf("%s_%d", prefix, time.Now().UnixNano())
}
