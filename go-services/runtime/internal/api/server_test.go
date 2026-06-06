package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"nanocursor/go-runtime/internal/config"
)

func TestHealth(t *testing.T) {
	server := NewServer(config.Config{Addr: "127.0.0.1:0", Version: "test"})
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	resp := httptest.NewRecorder()

	server.Routes().ServeHTTP(resp, req)
	if resp.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(resp.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body["service"] != "nanocursor-runtime" {
		t.Fatalf("unexpected service: %#v", body)
	}
}

func TestPreview(t *testing.T) {
	server := NewServer(config.Config{Addr: "127.0.0.1:0", Version: "test"})
	payload := map[string]any{
		"workspace_dir": t.TempDir(),
		"tool":          "run_command",
		"input": map[string]any{
			"command": "echo hello",
		},
		"python_policy": map[string]any{
			"permission_level": "shell_safe",
		},
	}
	raw, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/v1/tools/preview", bytes.NewReader(raw))
	resp := httptest.NewRecorder()

	server.Routes().ServeHTTP(resp, req)
	if resp.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", resp.Code, resp.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(resp.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body["allowed"] != true {
		t.Fatalf("expected allowed preview, got %#v", body)
	}
}

func TestPreviewRejectsOversizedJSONBody(t *testing.T) {
	server := NewServer(config.Config{Addr: "127.0.0.1:0", Version: "test"})
	req := httptest.NewRequest(
		http.MethodPost,
		"/v1/tools/preview",
		strings.NewReader(strings.Repeat("x", int(maxJSONBodyBytes)+1)),
	)
	resp := httptest.NewRecorder()

	server.Routes().ServeHTTP(resp, req)
	if resp.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", resp.Code, resp.Body.String())
	}
}

func TestCancelToolRun(t *testing.T) {
	server := NewServer(config.Config{Addr: "127.0.0.1:0", Version: "test"})
	payload := map[string]any{
		"workspace_dir": t.TempDir(),
		"tool":          "run_command",
		"input": map[string]any{
			"command": "sleep 5",
		},
		"policy": map[string]any{
			"permission_level": "shell_safe",
		},
	}
	raw, _ := json.Marshal(payload)
	createReq := httptest.NewRequest(http.MethodPost, "/v1/tools/execute", bytes.NewReader(raw))
	createResp := httptest.NewRecorder()
	server.Routes().ServeHTTP(createResp, createReq)
	if createResp.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d: %s", createResp.Code, createResp.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(createResp.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	toolRunID, _ := body["tool_run_id"].(string)
	if toolRunID == "" {
		t.Fatalf("missing tool_run_id: %#v", body)
	}

	cancelReq := httptest.NewRequest(http.MethodPost, "/v1/tools/runs/"+toolRunID+"/cancel", nil)
	cancelResp := httptest.NewRecorder()
	server.Routes().ServeHTTP(cancelResp, cancelReq)
	if cancelResp.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", cancelResp.Code, cancelResp.Body.String())
	}
}

func TestMcpToolCallUnregisteredServer(t *testing.T) {
	server := NewServer(config.Config{Addr: "127.0.0.1:0", Version: "test"})
	payload := map[string]any{
		"server_id":     "mcp.missing",
		"tool_name":     "read_echo",
		"workspace_dir": t.TempDir(),
	}
	raw, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/v1/mcp/tools/call", bytes.NewReader(raw))
	resp := httptest.NewRecorder()

	server.Routes().ServeHTTP(resp, req)
	if resp.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", resp.Code, resp.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(resp.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body["ok"] != false {
		t.Fatalf("expected failed mcp call, got %#v", body)
	}
}

func TestMcpPresets(t *testing.T) {
	server := NewServer(config.Config{Addr: "127.0.0.1:0", Version: "test"})
	req := httptest.NewRequest(http.MethodGet, "/v1/mcp/presets", nil)
	resp := httptest.NewRecorder()

	server.Routes().ServeHTTP(resp, req)
	if resp.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(resp.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	presets, ok := body["presets"].([]any)
	if !ok || len(presets) == 0 {
		t.Fatalf("expected presets, got %#v", body)
	}
}
