package server

import (
	"context"
	"fmt"
	"net"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"
	pb "nanocursor/go-services/cron/proto"
)

const bufSize = 1024 * 1024

func startTestServer(t *testing.T) (pb.CronServiceClient, func()) {
	t.Helper()
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer()
	srv := NewCronServer()
	srv.Init(t.TempDir())
	pb.RegisterCronServiceServer(s, srv)
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
	client := pb.NewCronServiceClient(conn)
	return client, func() {
		conn.Close()
		srv.Stop()
		s.Stop()
	}
}

func TestCronHealth(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.Health(context.Background(), &pb.HealthRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if !resp.Ok {
		t.Fatal("expected ok")
	}
	if resp.Service != "nanocursor-cron" {
		t.Fatalf("expected nanocursor-cron, got %s", resp.Service)
	}
}

func TestCronCreateAndList(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	task, err := client.CreateTask(context.Background(), &pb.CreateTaskRequest{
		CronExpr: "*/5 * * * *", Prompt: "test prompt", Recurring: true, Durable: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if task.Id == "" {
		t.Fatal("expected non-empty task ID")
	}
	if task.Prompt != "test prompt" {
		t.Fatalf("expected 'test prompt', got %s", task.Prompt)
	}
	list, err := client.ListTasks(context.Background(), &pb.ListTasksRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if len(list.Tasks) != 1 {
		t.Fatalf("expected 1 task, got %d", len(list.Tasks))
	}
}

func TestCronDelete(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	task, _ := client.CreateTask(context.Background(), &pb.CreateTaskRequest{
		CronExpr: "*/5 * * * *", Prompt: "to delete",
	})
	resp, err := client.DeleteTask(context.Background(), &pb.DeleteTaskRequest{TaskId: task.Id})
	if err != nil {
		t.Fatal(err)
	}
	if !resp.Success {
		t.Fatalf("expected success, got %v", resp)
	}
	list, _ := client.ListTasks(context.Background(), &pb.ListTasksRequest{})
	if len(list.Tasks) != 0 {
		t.Fatalf("expected 0 tasks, got %d", len(list.Tasks))
	}
}

func TestCronDeleteNonexistent(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	resp, err := client.DeleteTask(context.Background(), &pb.DeleteTaskRequest{TaskId: "nonexistent"})
	if err != nil {
		t.Fatal(err)
	}
	if resp.Success {
		t.Fatal("expected failure for nonexistent task")
	}
}

func TestCronDrainEvents(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()
	now := time.Now()
	expr := fmt.Sprintf("%d %d * * *", now.Minute(), now.Hour())
	client.CreateTask(context.Background(), &pb.CreateTaskRequest{
		CronExpr: expr, Prompt: "fire now", Recurring: false,
	})
	stream, err := client.DrainEvents(context.Background(), &pb.DrainEventsRequest{})
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan *pb.CronEvent, 1)
	go func() {
		ev, err := stream.Recv()
		if err != nil {
			return
		}
		done <- ev
	}()
	select {
	case ev := <-done:
		if ev.Type != "cron_fired" {
			t.Fatalf("expected cron_fired, got %s", ev.Type)
		}
		if ev.Prompt != "fire now" {
			t.Fatalf("expected 'fire now', got %s", ev.Prompt)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("timeout waiting for event")
	}
}
