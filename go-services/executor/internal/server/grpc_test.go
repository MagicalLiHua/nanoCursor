package server

import (
	"context"
	"io"
	"net"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"
	pb "nanocursor/go-services/executor/proto"
)

const bufSize = 1024 * 1024

func startTestServer(t *testing.T) (pb.ExecutorServiceClient, func()) {
	t.Helper()
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer()
	pb.RegisterExecutorServiceServer(s, NewExecutorServer())
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
	client := pb.NewExecutorServiceClient(conn)
	return client, func() {
		conn.Close()
		s.Stop()
	}
}

func TestHealth(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.Health(context.Background(), &pb.HealthRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if !resp.Ok {
		t.Fatal("expected ok")
	}
	if resp.Service != "nanocursor-executor" {
		t.Fatalf("expected nanocursor-executor, got %s", resp.Service)
	}
}

func TestPreviewSafeCommand(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.PreviewTool(context.Background(), &pb.PreviewRequest{
		Command:      "echo hello",
		Cwd:          "/tmp",
		WorkspaceDir: "/tmp",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !resp.Allowed {
		t.Fatalf("expected allowed, got error_code=%s message=%s", resp.ErrorCode, resp.Message)
	}
	if resp.PermissionLevel != "shell_safe" {
		t.Fatalf("expected shell_safe, got %s", resp.PermissionLevel)
	}
}

func TestPreviewRiskyCommandDenied(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.PreviewTool(context.Background(), &pb.PreviewRequest{
		Command:      "rm -rf /tmp/test",
		Cwd:          "/tmp",
		WorkspaceDir: "/tmp",
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.Allowed {
		t.Fatal("expected denied for risky command without token")
	}
	if resp.ErrorCode != "approval_required" {
		t.Fatalf("expected approval_required, got %s", resp.ErrorCode)
	}
}

func TestExecuteAndPoll(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	run, err := client.ExecuteTool(context.Background(), &pb.ExecuteRequest{
		Command:         "echo hello",
		Cwd:             "/tmp",
		WorkspaceDir:    "/tmp",
		TimeoutMs:       5000,
		PermissionLevel: "shell_safe",
	})
	if err != nil {
		t.Fatal(err)
	}
	if run.Status != "running" {
		t.Fatalf("expected running, got %s", run.Status)
	}

	var final *pb.ToolRun
	for range 50 {
		final, err = client.GetToolRun(context.Background(), &pb.GetToolRunRequest{Id: run.Id})
		if err != nil {
			t.Fatal(err)
		}
		if final.Status != "running" {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if final.Status != "completed" {
		t.Fatalf("expected completed, got %s", final.Status)
	}
	if final.ExitCode != 0 {
		t.Fatalf("expected exit 0, got %d", final.ExitCode)
	}
	if final.Stdout == "" {
		t.Fatal("expected stdout")
	}
}

func TestStreamEvents(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	run, err := client.ExecuteTool(context.Background(), &pb.ExecuteRequest{
		Command:         "echo streaming",
		Cwd:             "/tmp",
		WorkspaceDir:    "/tmp",
		TimeoutMs:       5000,
		PermissionLevel: "shell_safe",
	})
	if err != nil {
		t.Fatal(err)
	}

	stream, err := client.StreamToolRunEvents(context.Background(), &pb.StreamEventsRequest{
		RunId:       run.Id,
		AfterCursor: 0,
	})
	if err != nil {
		t.Fatal(err)
	}

	var events []*pb.ToolEvent
	for {
		ev, err := stream.Recv()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		events = append(events, ev)
	}
	if len(events) < 2 {
		t.Fatalf("expected at least 2 events, got %d", len(events))
	}
	if events[0].Type != "tool.started" {
		t.Fatalf("expected tool.started, got %s", events[0].Type)
	}
}

func TestCancelRun(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	run, err := client.ExecuteTool(context.Background(), &pb.ExecuteRequest{
		Command:         "sleep 10",
		Cwd:             "/tmp",
		WorkspaceDir:    "/tmp",
		TimeoutMs:       15000,
		PermissionLevel: "shell_safe",
	})
	if err != nil {
		t.Fatal(err)
	}

	result, err := client.CancelToolRun(context.Background(), &pb.CancelRequest{RunId: run.Id})
	if err != nil {
		t.Fatal(err)
	}
	if !result.Success {
		t.Fatalf("expected cancel success, got %v", result)
	}

	time.Sleep(300 * time.Millisecond)
	final, err := client.GetToolRun(context.Background(), &pb.GetToolRunRequest{Id: run.Id})
	if err != nil {
		t.Fatal(err)
	}
	if final.Status != "cancelled" && final.Status != "failed" {
		t.Fatalf("expected cancelled or failed, got %s", final.Status)
	}
}

func TestWorkspaceBoundaryViolation(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.PreviewTool(context.Background(), &pb.PreviewRequest{
		Command:      "echo hello",
		Cwd:          "/etc",
		WorkspaceDir: "/tmp",
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.Allowed {
		t.Fatal("expected denied for cwd outside workspace")
	}
	if resp.ErrorCode != "workspace_boundary_violation" {
		t.Fatalf("expected workspace_boundary_violation, got %s", resp.ErrorCode)
	}
}
