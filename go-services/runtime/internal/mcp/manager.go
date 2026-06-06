package mcp

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"nanocursor/go-runtime/internal/policy"
	"nanocursor/go-runtime/internal/supervisor"
)

type Manager struct {
	mu      sync.RWMutex
	servers map[string]ProbeRequest
	limiter *supervisor.Limiter
}

func NewManager() *Manager {
	return newManagerWithLimits(4, 2)
}

func newManagerWithLimits(maxWorkspaceRuns int, maxRunTools int) *Manager {
	return &Manager{
		servers: map[string]ProbeRequest{},
		limiter: supervisor.NewLimiter(maxWorkspaceRuns, maxRunTools),
	}
}

func (m *Manager) Presets() []Preset {
	return Presets()
}

func (m *Manager) Probe(req ProbeRequest) ProbeResult {
	checks := []ProbeCheck{}
	status := "passed"
	if req.ServerID == "" {
		checks = append(checks, ProbeCheck{ID: "server_id", Status: "failed", Message: "server_id is required"})
		status = "failed"
	}
	if req.Enabled != nil && !*req.Enabled {
		checks = append(checks, ProbeCheck{ID: "enabled", Status: "warning", Message: "server is disabled"})
		if status != "failed" {
			status = "warning"
		}
	}
	if req.WorkspaceDir != "" {
		if _, err := filepath.Abs(req.WorkspaceDir); err != nil {
			checks = append(checks, ProbeCheck{ID: "workspace", Status: "failed", Message: "workspace_dir is invalid"})
			status = "failed"
		} else if _, err := os.Stat(req.WorkspaceDir); err != nil {
			checks = append(checks, ProbeCheck{ID: "workspace", Status: "failed", Message: "workspace_dir does not exist"})
			status = "failed"
		} else {
			checks = append(checks, ProbeCheck{ID: "workspace", Status: "passed", Message: "workspace_dir exists"})
		}
	}
	if strings.TrimSpace(req.Command) == "" {
		checks = append(checks, ProbeCheck{ID: "command", Status: "failed", Message: "command is required"})
		status = "failed"
	} else if _, err := exec.LookPath(req.Command); err != nil {
		checks = append(checks, ProbeCheck{ID: "command", Status: "failed", Message: "command not found on PATH"})
		status = "failed"
	} else {
		checks = append(checks, ProbeCheck{ID: "command", Status: "passed", Message: "command found on PATH"})
	}
	env := req.Env
	if env == nil {
		env = map[string]string{}
	}
	for _, key := range req.EnvKeys {
		value := env[key]
		if value == "" {
			value = os.Getenv(key)
		}
		if value == "" {
			checks = append(checks, ProbeCheck{ID: "env_" + key, Status: "warning", Message: "environment variable is not set"})
			if status != "failed" {
				status = "warning"
			}
		} else {
			checks = append(checks, ProbeCheck{ID: "env_" + key, Status: "passed", Message: "environment variable is present"})
		}
	}
	result := ProbeResult{
		ServerID: req.ServerID,
		Status:   status,
		Ok:       status == "passed" || status == "warning",
		Checks:   checks,
		Command:  req.Command,
		Args:     req.Args,
	}
	if result.Ok && req.ServerID != "" {
		m.mu.Lock()
		m.servers[req.ServerID] = req
		m.mu.Unlock()
	}
	return result
}

func (m *Manager) Servers() []map[string]any {
	m.mu.RLock()
	defer m.mu.RUnlock()
	servers := make([]map[string]any, 0, len(m.servers))
	for id, cfg := range m.servers {
		servers = append(servers, map[string]any{
			"server_id": id,
			"command":   cfg.Command,
			"args":      cfg.Args,
		})
	}
	return servers
}

func (m *Manager) Tools(serverID string) ToolCatalog {
	cfg, ok := m.serverConfig(serverID)
	if !ok {
		return ToolCatalog{
			ServerID: serverID,
			Status:   "not_connected",
			Ok:       false,
			Tools:    []ToolInfo{},
			Error:    "MCP server has not been probed or registered",
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	slot, snapshot, ok := m.acquireSlot(cfg.WorkspaceDir, "mcp:"+serverID+":tools")
	if !ok {
		return ToolCatalog{
			ServerID: serverID,
			Status:   "busy",
			Ok:       false,
			Tools:    []ToolInfo{},
			Error:    busyMessage(snapshot),
		}
	}
	defer slot.Release()
	client, err := newStdioClient(ctx, cfg)
	if err != nil {
		return ToolCatalog{ServerID: serverID, Status: "failed", Ok: false, Tools: []ToolInfo{}, Error: err.Error()}
	}
	defer client.Close()
	tools, err := client.ListTools(ctx)
	if err != nil {
		return ToolCatalog{ServerID: serverID, Status: "failed", Ok: false, Tools: []ToolInfo{}, Error: err.Error()}
	}
	return ToolCatalog{
		ServerID: serverID,
		Status:   "ready",
		Ok:       true,
		Tools:    tools,
	}
}

func (m *Manager) Call(req CallRequest) map[string]any {
	cfg, ok := m.serverConfig(req.ServerID)
	if !ok {
		return map[string]any{"server_id": req.ServerID, "tool": req.ToolName, "ok": false, "error": "MCP server has not been probed or registered"}
	}
	workspaceDir := strings.TrimSpace(req.WorkspaceDir)
	if workspaceDir == "" {
		workspaceDir = cfg.WorkspaceDir
	}
	permissionLevel := strings.TrimSpace(req.Policy.PermissionLevel)
	if permissionLevel == "" {
		permissionLevel = classifyToolPermission(req.ToolName)
	}
	requiresApproval := req.Policy.RequiresApproval || permissionLevel == "mcp_write" || permissionLevel == "external_risky"
	target := req.ServerID + "/" + req.ToolName
	decision := policy.Preview(policy.Input{
		WorkspaceDir:     workspaceDir,
		Command:          target,
		PermissionLevel:  permissionLevel,
		RequiresApproval: requiresApproval,
		ApprovalID:       req.Policy.ApprovalID,
		ApprovalToken:    req.Policy.ApprovalToken,
	})
	if !decision.Allowed {
		return map[string]any{
			"server_id":         req.ServerID,
			"tool":              req.ToolName,
			"ok":                false,
			"status":            "denied",
			"error":             decision.Message,
			"error_code":        decision.ErrorCode,
			"permission_level":  decision.PermissionLevel,
			"requires_approval": decision.RequiresApproval,
			"reasons":           decision.Reasons,
		}
	}
	slot, snapshot, ok := m.acquireSlot(workspaceDir, req.RunID)
	if !ok {
		return map[string]any{
			"server_id":          req.ServerID,
			"tool":               req.ToolName,
			"ok":                 false,
			"status":             "failed",
			"error":              busyMessage(snapshot),
			"error_code":         "runtime_busy",
			"workspace_active":   snapshot.WorkspaceActive,
			"run_active":         snapshot.RunActive,
			"max_workspace_runs": snapshot.MaxWorkspace,
			"max_run_tools":      snapshot.MaxRun,
		}
	}
	defer slot.Release()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	client, err := newStdioClient(ctx, cfg)
	if err != nil {
		return map[string]any{"server_id": req.ServerID, "tool": req.ToolName, "ok": false, "error": err.Error()}
	}
	defer client.Close()
	arguments := req.Arguments
	if arguments == nil {
		arguments = map[string]any{}
	}
	result, err := client.CallTool(ctx, req.ToolName, arguments)
	if err != nil {
		return map[string]any{"server_id": req.ServerID, "tool": req.ToolName, "ok": false, "error": err.Error()}
	}
	return map[string]any{
		"server_id":         req.ServerID,
		"tool":              req.ToolName,
		"ok":                true,
		"result":            result,
		"permission_level":  decision.PermissionLevel,
		"requires_approval": decision.RequiresApproval,
	}
}

func (m *Manager) acquireSlot(workspaceDir string, runID string) (*supervisor.LimitSlot, supervisor.LimitSnapshot, bool) {
	return m.limiter.TryAcquire(supervisor.LimitScope{Workspace: workspaceDir, RunID: runID})
}

func busyMessage(snapshot supervisor.LimitSnapshot) string {
	return "Go runtime is busy for this workspace or run"
}

func (m *Manager) serverConfig(serverID string) (ProbeRequest, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	cfg, ok := m.servers[serverID]
	return cfg, ok
}

func (m *Manager) StaticTools(serverID string) ToolCatalog {
	return ToolCatalog{
		ServerID: serverID,
		Status:   "not_connected",
		Ok:       false,
		Tools:    []ToolInfo{},
		Error:    "MCP stdio process manager is not enabled in this phase",
	}
}

func classifyToolPermission(toolName string) string {
	lowered := strings.ToLower(strings.ReplaceAll(toolName, "-", "_"))
	for _, token := range []string{
		"create",
		"update",
		"delete",
		"remove",
		"write",
		"edit",
		"mutate",
		"submit",
		"approve",
		"merge",
		"commit",
		"push",
		"post",
		"upload",
		"install",
	} {
		if strings.Contains(lowered, token) {
			return "mcp_write"
		}
	}
	for _, prefix := range []string{
		"list",
		"get",
		"read",
		"search",
		"find",
		"query",
		"fetch",
		"inspect",
		"describe",
		"resolve",
		"lookup",
	} {
		if strings.HasPrefix(lowered, prefix) || strings.Contains(lowered, "_"+prefix) {
			return "mcp_read"
		}
	}
	return "external_risky"
}
