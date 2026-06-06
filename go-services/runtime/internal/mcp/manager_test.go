package mcp

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestPresetsIncludesFilesystem(t *testing.T) {
	manager := NewManager()
	presets := manager.Presets()
	if len(presets) == 0 {
		t.Fatal("expected presets")
	}
	found := false
	for _, preset := range presets {
		if preset.ID == "filesystem" && preset.ServerID == "mcp.filesystem" {
			found = true
		}
	}
	if !found {
		t.Fatalf("filesystem preset missing: %#v", presets)
	}
}

func TestProbeEchoCommand(t *testing.T) {
	manager := NewManager()
	result := manager.Probe(ProbeRequest{
		ServerID:     "mcp.echo",
		WorkspaceDir: t.TempDir(),
		Command:      "echo",
	})
	if !result.Ok {
		t.Fatalf("expected probe ok, got %#v", result)
	}
}

func TestProbeMissingCommandFails(t *testing.T) {
	manager := NewManager()
	result := manager.Probe(ProbeRequest{
		ServerID:     "mcp.missing",
		WorkspaceDir: t.TempDir(),
		Command:      "command-that-does-not-exist-nanocursor",
	})
	if result.Status != "failed" {
		t.Fatalf("expected failed probe, got %#v", result)
	}
}

func TestListToolsAndCallFakeMCPServer(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		python, err = exec.LookPath("python")
	}
	if err != nil {
		t.Skip("python is not available")
	}
	workspace := t.TempDir()
	script := filepath.Join(workspace, "fake_mcp.py")
	if err := os.WriteFile(script, []byte(fakeMCPServerSource), 0o644); err != nil {
		t.Fatal(err)
	}
	manager := NewManager()
	probe := manager.Probe(ProbeRequest{
		ServerID:     "mcp.fake",
		WorkspaceDir: workspace,
		Command:      python,
		Args:         []string{script},
	})
	if !probe.Ok {
		t.Fatalf("probe failed: %#v", probe)
	}
	catalog := manager.Tools("mcp.fake")
	if !catalog.Ok {
		t.Fatalf("tools failed: %#v", catalog)
	}
	if len(catalog.Tools) != 2 {
		t.Fatalf("expected two tools, got %#v", catalog.Tools)
	}
	call := manager.Call(CallRequest{
		ServerID:  "mcp.fake",
		ToolName:  "read_echo",
		Arguments: map[string]any{"text": "hi"},
	})
	if call["ok"] != true {
		t.Fatalf("call failed: %#v", call)
	}
	writeCall := manager.Call(CallRequest{
		ServerID:  "mcp.fake",
		ToolName:  "write_note",
		Arguments: map[string]any{"text": "hi"},
	})
	if writeCall["ok"] != false || writeCall["error_code"] != "approval_required" {
		t.Fatalf("expected write tool to require approval, got %#v", writeCall)
	}
}

func TestCallRejectsWhenMCPWorkspaceBusy(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		python, err = exec.LookPath("python")
	}
	if err != nil {
		t.Skip("python is not available")
	}
	workspace := t.TempDir()
	script := filepath.Join(workspace, "fake_mcp.py")
	if err := os.WriteFile(script, []byte(fakeMCPServerSource), 0o644); err != nil {
		t.Fatal(err)
	}
	manager := newManagerWithLimits(1, 1)
	probe := manager.Probe(ProbeRequest{
		ServerID:     "mcp.fake",
		WorkspaceDir: workspace,
		Command:      python,
		Args:         []string{script},
	})
	if !probe.Ok {
		t.Fatalf("probe failed: %#v", probe)
	}

	done := make(chan map[string]any, 1)
	go func() {
		done <- manager.Call(CallRequest{
			ServerID:     "mcp.fake",
			RunID:        "run-a",
			ToolName:     "read_echo",
			WorkspaceDir: workspace,
			Arguments:    map[string]any{"text": "slow"},
		})
	}()
	time.Sleep(50 * time.Millisecond)

	busy := manager.Call(CallRequest{
		ServerID:     "mcp.fake",
		RunID:        "run-b",
		ToolName:     "read_echo",
		WorkspaceDir: workspace,
		Arguments:    map[string]any{"text": "hi"},
	})
	if busy["ok"] != false || busy["error_code"] != "runtime_busy" {
		t.Fatalf("expected runtime_busy, got %#v", busy)
	}

	first := <-done
	if first["ok"] != true {
		t.Fatalf("expected first call success, got %#v", first)
	}
}

const fakeMCPServerSource = `
import json
import sys
import time

def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("ascii").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))

def write_message(message):
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()

while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}})
    elif method == "tools/list":
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": "read_echo", "description": "Read echo"}, {"name": "write_note", "description": "Write note"}]}})
    elif method == "tools/call":
        params = message.get("params", {})
        arguments = params.get("arguments", {})
        if arguments.get("text") == "slow":
            time.sleep(0.25)
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "echo:" + str(arguments.get("text", ""))}]}})
    else:
        write_message({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})
`
