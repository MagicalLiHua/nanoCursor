package server

import (
	"context"
	"net"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"
)

const bufSize = 1024 * 1024

func startTestServer(t *testing.T) (*grpc.ClientConn, func()) {
	t.Helper()

	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer()
	RegisterPolicyServer(s, NewPolicyServer())

	go func() {
		if err := s.Serve(lis); err != nil {
			t.Errorf("server exited with error: %v", err)
		}
	}()

	conn, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return lis.DialContext(ctx)
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}

	cleanup := func() {
		conn.Close()
		s.Stop()
	}

	return conn, cleanup
}

func TestHealth(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()

	client := NewPolicyClient(conn)
	resp, err := client.Health(context.Background(), &HealthRequest{})
	if err != nil {
		t.Fatalf("Health failed: %v", err)
	}
	if !resp.Ok {
		t.Error("expected ok=true")
	}
	if resp.Service != "nanocursor-policy" {
		t.Errorf("expected service=nanocursor-policy, got %s", resp.Service)
	}
}

func TestCheckTool(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()

	client := NewPolicyClient(conn)

	tests := []struct {
		tool         string
		input        string
		wantDecision string
	}{
		{"read_file", "", "allow"},
		{"write_file", "", "allow"},
		{"delete_file", "", "require_approval"},
		{"bash", "ls -la", "allow"},
		{"bash", "rm -rf /", "require_approval"},
	}

	for _, tt := range tests {
		resp, err := client.CheckTool(context.Background(), &CheckToolRequest{
			ToolName:  tt.tool,
			ToolInput: tt.input,
		})
		if err != nil {
			t.Fatalf("CheckTool(%q) error: %v", tt.tool, err)
		}
		if resp.Decision != tt.wantDecision {
			t.Errorf("CheckTool(%q, %q) = %q, want %q", tt.tool, tt.input, resp.Decision, tt.wantDecision)
		}
	}
}

func TestCheckAction(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()

	client := NewPolicyClient(conn)

	resp, err := client.CheckAction(context.Background(), &CheckActionRequest{
		Command: "git status",
	})
	if err != nil {
		t.Fatalf("CheckAction error: %v", err)
	}
	if resp.Decision != "allow" {
		t.Errorf("git status: decision = %q, want allow", resp.Decision)
	}
	if resp.CommandType != "shell_safe" {
		t.Errorf("git status: command_type = %q, want shell_safe", resp.CommandType)
	}
}

func TestRecordAndGetState(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()

	client := NewPolicyClient(conn)

	// Record 3 failures to trigger escalation
	for i := 0; i < 3; i++ {
		client.RecordResult(context.Background(), &RecordResultRequest{
			ToolName: "bash",
			Success:  false,
			RunId:    "test-run",
		})
	}

	state, err := client.GetPolicyState(context.Background(), &GetPolicyStateRequest{
		RunId: "test-run",
	})
	if err != nil {
		t.Fatalf("GetPolicyState error: %v", err)
	}
	if state.ConsecutiveFailures != 3 {
		t.Errorf("consecutive_failures = %d, want 3", state.ConsecutiveFailures)
	}
	if state.TotalFailures != 3 {
		t.Errorf("total_failures = %d, want 3", state.TotalFailures)
	}
}
