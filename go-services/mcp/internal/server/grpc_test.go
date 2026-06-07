package server

import (
	"context"
	"net"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"
	pb "nanocursor/go-services/mcp/proto"
)

const bufSize = 1024 * 1024

func startTestServer(t *testing.T) (pb.MCPServiceClient, func()) {
	t.Helper()
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer()
	pb.RegisterMCPServiceServer(s, NewMCPServer())
	go func() {
		if err := s.Serve(lis); err != nil {
			t.Logf("server exited: %v", err)
		}
	}()
	conn, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return lis.DialContext(ctx)
		}),
	)
	if err != nil {
		t.Fatal(err)
	}
	client := pb.NewMCPServiceClient(conn)
	return client, func() {
		conn.Close()
		s.Stop()
	}
}

func TestMCPHealth(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.Health(context.Background(), &pb.HealthRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if !resp.Ok {
		t.Fatal("expected ok")
	}
	if resp.Service != "nanocursor-mcp" {
		t.Fatalf("expected nanocursor-mcp, got %s", resp.Service)
	}
}

func TestListPresets(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.ListPresets(context.Background(), &pb.ListPresetsRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if len(resp.Presets) != 5 {
		t.Fatalf("expected 5 presets, got %d", len(resp.Presets))
	}
	ids := make(map[string]bool)
	for _, p := range resp.Presets {
		ids[p.Id] = true
	}
	for _, expected := range []string{"filesystem", "docs", "memory", "sequential-thinking", "github"} {
		if !ids[expected] {
			t.Fatalf("missing preset: %s", expected)
		}
	}
}

func TestProbeAndListServers(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()

	probeResp, err := client.ProbeServer(context.Background(), &pb.ProbeRequest{
		ServerId: "test.echo",
		Command:  "echo",
		Args:     []string{"hello"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !probeResp.Ok {
		t.Fatalf("expected probe ok, got %v", probeResp)
	}

	listResp, err := client.ListServers(context.Background(), &pb.ListServersRequest{})
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, srv := range listResp.Servers {
		if srv.Id == "test.echo" {
			found = true
			break
		}
	}
	if !found {
		t.Fatal("expected test.echo in server list")
	}
}

func TestProbeMissingCommand(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.ProbeServer(context.Background(), &pb.ProbeRequest{
		ServerId:     "bad.server",
		Command:      "nonexistent_command_xyz",
		WorkspaceDir: "/tmp",
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.Ok {
		t.Fatal("expected probe to fail for nonexistent command")
	}
}

func TestCallToolWithoutProbe(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.CallTool(context.Background(), &pb.CallToolRequest{
		ServerId: "unregistered.server",
		ToolName: "test_tool",
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.Ok {
		t.Fatal("expected call to fail for unregistered server")
	}
}
