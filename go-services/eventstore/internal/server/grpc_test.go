package server

import (
	"context"
	"net"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"
	pb "nanocursor/go-services/eventstore/proto"
)

const bufSize = 1024 * 1024

func startTestServer(t *testing.T) (pb.EventStoreServiceClient, string, func()) {
	t.Helper()
	workspace := t.TempDir()
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer()
	pb.RegisterEventStoreServiceServer(s, NewEventStoreServer(workspace))
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
	client := pb.NewEventStoreServiceClient(conn)
	return client, workspace, func() {
		conn.Close()
		s.Stop()
	}
}

func TestHealth(t *testing.T) {
	client, _, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.Health(context.Background(), &pb.HealthRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if !resp.Ok || resp.Service != "nanocursor-eventstore" {
		t.Fatalf("unexpected health: %v", resp)
	}
}

func TestSessionCRUD(t *testing.T) {
	client, workspace, cleanup := startTestServer(t)
	defer cleanup()

	session, err := client.CreateSession(context.Background(), &pb.CreateSessionRequest{
		ThreadId: "t1", Prompt: "hello", WorkspaceDir: workspace,
	})
	if err != nil {
		t.Fatal(err)
	}
	if session.ThreadId != "t1" || session.Status != "running" {
		t.Fatalf("unexpected session: %v", session)
	}

	got, _ := client.GetSession(context.Background(), &pb.GetSessionRequest{ThreadId: "t1", WorkspaceDir: workspace})
	if got.Prompt != "hello" {
		t.Fatalf("expected hello, got %s", got.Prompt)
	}

	updated, _ := client.UpdateSession(context.Background(), &pb.UpdateSessionRequest{
		ThreadId: "t1", WorkspaceDir: workspace,
		Changes: map[string]string{"status": "completed"},
	})
	if updated.Status != "completed" {
		t.Fatalf("expected completed, got %s", updated.Status)
	}
}

func TestAppendAndListEvents(t *testing.T) {
	client, workspace, cleanup := startTestServer(t)
	defer cleanup()

	client.AppendEvent(context.Background(), &pb.AppendEventRequest{
		ThreadId: "t1", EventType: "message", Title: "hi", WorkspaceDir: workspace,
	})
	client.AppendEvent(context.Background(), &pb.AppendEventRequest{
		ThreadId: "t1", EventType: "done", WorkspaceDir: workspace,
	})

	list, _ := client.ListEvents(context.Background(), &pb.ListEventsRequest{
		ThreadId: "t1", WorkspaceDir: workspace,
	})
	if len(list.Events) != 2 {
		t.Fatalf("expected 2 events, got %d", len(list.Events))
	}

	count, _ := client.CountEvents(context.Background(), &pb.CountEventsRequest{
		ThreadId: "t1", WorkspaceDir: workspace,
	})
	if count.Count != 2 {
		t.Fatalf("expected 2, got %d", count.Count)
	}
}

func TestSubscribeEvents(t *testing.T) {
	client, workspace, cleanup := startTestServer(t)
	defer cleanup()

	stream, err := client.SubscribeEvents(context.Background(), &pb.SubscribeEventsRequest{
		ThreadId: "t1", WorkspaceDir: workspace,
	})
	if err != nil {
		t.Fatal(err)
	}

	go func() {
		time.Sleep(100 * time.Millisecond)
		client.AppendEvent(context.Background(), &pb.AppendEventRequest{
			ThreadId: "t1", EventType: "test", WorkspaceDir: workspace,
		})
	}()

	event, err := stream.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if event.Type != "test" {
		t.Fatalf("expected test, got %s", event.Type)
	}
}

func TestWorkspaceForThread(t *testing.T) {
	client, workspace, cleanup := startTestServer(t)
	defer cleanup()

	client.CreateSession(context.Background(), &pb.CreateSessionRequest{
		ThreadId: "t1", WorkspaceDir: workspace,
	})
	resp, _ := client.WorkspaceForThread(context.Background(), &pb.WorkspaceForThreadRequest{ThreadId: "t1"})
	if !resp.Found {
		t.Fatal("expected to find workspace")
	}
}
