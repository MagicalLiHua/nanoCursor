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
	RegisterTaskBoardServer(s, NewTaskBoardServer())
	go func() {
		if err := s.Serve(lis); err != nil {
			t.Errorf("server error: %v", err)
		}
	}()
	conn, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return lis.DialContext(ctx)
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	return conn, func() { conn.Close(); s.Stop() }
}

func TestHealth(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := NewTaskBoardClient(conn)
	resp, err := client.Health(context.Background(), &HealthRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if !resp.Ok {
		t.Error("expected ok=true")
	}
	if resp.Service != "nanocursor-taskboard" {
		t.Errorf("service = %q, want nanocursor-taskboard", resp.Service)
	}
}

func TestAddAndGetTask(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := NewTaskBoardClient(conn)
	runID := "test-run-1"

	_, err := client.AddTask(context.Background(), &AddTaskRequest{
		RunId: runID,
		Task: &TaskMessage{
			Id: "t1", Type: "analysis", Title: "Analyze",
			Status: "pending", AgentRole: "lead",
		},
		Reason: "test",
	})
	if err != nil {
		t.Fatal(err)
	}

	resp, err := client.GetTask(context.Background(), &GetTaskRequest{RunId: runID, TaskId: "t1"})
	if err != nil {
		t.Fatal(err)
	}
	if !resp.Found {
		t.Fatal("expected task to be found")
	}
	if resp.Task.Title != "Analyze" {
		t.Errorf("title = %q", resp.Task.Title)
	}
}

func TestApplyStatusAndReadyNodes(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := NewTaskBoardClient(conn)
	runID := "test-run-2"

	client.AddTask(context.Background(), &AddTaskRequest{
		RunId: runID,
		Task: &TaskMessage{Id: "t1", Type: "analysis", Title: "A", Status: "passed"},
	})
	client.AddTask(context.Background(), &AddTaskRequest{
		RunId: runID,
		Task: &TaskMessage{Id: "t2", Type: "test", Title: "B", Status: "pending", Dependencies: []string{"t1"}},
	})

	resp, err := client.GetReadyNodes(context.Background(), &GetReadyNodesRequest{RunId: runID})
	if err != nil {
		t.Fatal(err)
	}
	if len(resp.Tasks) != 1 || resp.Tasks[0].Id != "t2" {
		t.Errorf("expected [t2], got %v", resp.Tasks)
	}
	if resp.Tasks[0].Status != "ready" {
		t.Errorf("t2 status = %q, want ready", resp.Tasks[0].Status)
	}
}

func TestSaveAndLoadBoard(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := NewTaskBoardClient(conn)
	runID := "test-run-3"
	runDir := t.TempDir()

	client.AddTask(context.Background(), &AddTaskRequest{
		RunId: runID,
		Task: &TaskMessage{Id: "t1", Type: "analysis", Title: "A", Status: "pending"},
	})

	saveResp, err := client.SaveBoard(context.Background(), &SaveBoardRequest{RunId: runID, RunDir: runDir})
	if err != nil {
		t.Fatal(err)
	}
	if saveResp.Path == "" {
		t.Error("expected non-empty path")
	}

	loadResp, err := client.LoadBoard(context.Background(), &LoadBoardRequest{RunId: runID, RunDir: runDir})
	if err != nil {
		t.Fatal(err)
	}
	if !loadResp.Found {
		t.Error("expected board to be loaded")
	}
}

func TestBuildBoard(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := NewTaskBoardClient(conn)

	plan := `{"strategy":"feature_delivery","stages":[{"id":"intake","title":"接收"}]}`
	_, err := client.BuildBoard(context.Background(), &BuildBoardRequest{
		RunId: "run-build", ExecutionPlanJson: plan, ConversationId: "conv-1",
	})
	if err != nil {
		t.Fatal(err)
	}

	state, err := client.GetBoardState(context.Background(), &GetBoardStateRequest{RunId: "run-build"})
	if err != nil {
		t.Fatal(err)
	}
	if state.Board.Strategy != "feature_delivery" {
		t.Errorf("strategy = %q", state.Board.Strategy)
	}
	if len(state.Board.Nodes) < 2 {
		t.Errorf("expected at least 2 nodes, got %d", len(state.Board.Nodes))
	}
}

func TestListTasks(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := NewTaskBoardClient(conn)
	runID := "test-run-list"

	client.AddTask(context.Background(), &AddTaskRequest{
		RunId: runID,
		Task: &TaskMessage{Id: "t1", Type: "analysis", Title: "A", Status: "pending"},
	})
	client.AddTask(context.Background(), &AddTaskRequest{
		RunId: runID,
		Task: &TaskMessage{Id: "t2", Type: "test", Title: "B", Status: "pending"},
	})

	resp, err := client.ListTasks(context.Background(), &ListTasksRequest{RunId: runID})
	if err != nil {
		t.Fatal(err)
	}
	if len(resp.Tasks) != 2 {
		t.Errorf("expected 2 tasks, got %d", len(resp.Tasks))
	}
}

func TestRemoveTask(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := NewTaskBoardClient(conn)
	runID := "test-run-remove"

	client.AddTask(context.Background(), &AddTaskRequest{
		RunId: runID,
		Task: &TaskMessage{Id: "t1", Type: "analysis", Title: "A", Status: "pending"},
	})

	_, err := client.RemoveTask(context.Background(), &RemoveTaskRequest{RunId: runID, TaskId: "t1", Reason: "test"})
	if err != nil {
		t.Fatal(err)
	}

	resp, _ := client.GetTask(context.Background(), &GetTaskRequest{RunId: runID, TaskId: "t1"})
	if resp.Found {
		t.Error("expected task to be removed")
	}
}

func TestConnectTasks(t *testing.T) {
	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := NewTaskBoardClient(conn)
	runID := "test-run-connect"

	client.AddTask(context.Background(), &AddTaskRequest{
		RunId: runID,
		Task: &TaskMessage{Id: "t1", Type: "analysis", Title: "A", Status: "pending"},
	})
	client.AddTask(context.Background(), &AddTaskRequest{
		RunId: runID,
		Task: &TaskMessage{Id: "t2", Type: "test", Title: "B", Status: "pending"},
	})

	_, err := client.ConnectTasks(context.Background(), &ConnectTasksRequest{
		RunId: runID, UpstreamTask: "t1", DownstreamTask: "t2", Reason: "test",
	})
	if err != nil {
		t.Fatal(err)
	}

	resp, _ := client.GetTask(context.Background(), &GetTaskRequest{RunId: runID, TaskId: "t2"})
	if len(resp.Task.Dependencies) != 1 || resp.Task.Dependencies[0] != "t1" {
		t.Errorf("expected t2 to depend on t1, got %v", resp.Task.Dependencies)
	}
}
