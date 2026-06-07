package mcp

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Manager struct {
	mu      sync.RWMutex
	servers map[string]ProbeRequest
}

func NewManager() *Manager {
	return &Manager{
		servers: map[string]ProbeRequest{},
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
		"server_id": req.ServerID,
		"tool":      req.ToolName,
		"ok":        true,
		"result":    result,
	}
}

func (m *Manager) serverConfig(serverID string) (ProbeRequest, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	cfg, ok := m.servers[serverID]
	return cfg, ok
}
