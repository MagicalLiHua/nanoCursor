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
	RegisterIndexerServer(s, NewIndexerServer())

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

	client := NewIndexerClient(conn)
	resp, err := client.Health(context.Background(), &HealthRequest{})
	if err != nil {
		t.Fatalf("Health failed: %v", err)
	}
	if !resp.Ok {
		t.Error("expected ok=true")
	}
	if resp.Service != "nanocursor-indexer" {
		t.Errorf("expected service=nanocursor-indexer, got %s", resp.Service)
	}
}

func TestBuildAndGetSummary(t *testing.T) {
	// This test requires a real workspace directory with files.
	// Skip if running without test workspace.
	t.Skip("requires test workspace setup - run manually with a test directory")
}
