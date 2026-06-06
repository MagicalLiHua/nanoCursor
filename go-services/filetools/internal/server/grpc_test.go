package server

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"
	pb "nanocursor/go-services/filetools/proto"
)

const bufSize = 1024 * 1024

func startTestServer(t *testing.T) (*grpc.ClientConn, func()) {
	t.Helper()
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer()
	pb.RegisterFileToolsServer(s, NewFileToolsServer())
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
	client := pb.NewFileToolsClient(conn)
	resp, err := client.Health(context.Background(), &pb.HealthRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if !resp.Ok {
		t.Error("expected ok=true")
	}
	if resp.Service != "nanocursor-filetools" {
		t.Errorf("service = %q", resp.Service)
	}
}

func TestReadFile(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "hello.txt"), []byte("world"), 0644)

	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := pb.NewFileToolsClient(conn)

	resp, err := client.ReadFile(context.Background(), &pb.ReadFileRequest{
		Workspace: dir, Filename: "hello.txt",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(resp.Content, "world") {
		t.Error("expected 'world' in content")
	}
}

func TestWriteFile(t *testing.T) {
	dir := t.TempDir()

	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := pb.NewFileToolsClient(conn)

	resp, err := client.WriteFile(context.Background(), &pb.WriteFileRequest{
		Workspace: dir, Filename: "new.txt", Content: "hello",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(resp.Message, "Successfully") {
		t.Error("expected success message")
	}

	content, _ := os.ReadFile(filepath.Join(dir, "new.txt"))
	if string(content) != "hello" {
		t.Errorf("content = %q", string(content))
	}
}

func TestEditFile(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "edit.txt"), []byte("line1\nold\nline3"), 0644)

	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := pb.NewFileToolsClient(conn)

	resp, err := client.EditFile(context.Background(), &pb.EditFileRequest{
		Workspace: dir, Filename: "edit.txt",
		SearchBlock: "old", ReplaceBlock: "new",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(resp.Result, "成功修改") {
		t.Errorf("expected success: %s", resp.Result)
	}
}

func TestListDirectory(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "a.txt"), []byte(""), 0644)
	os.Mkdir(filepath.Join(dir, "sub"), 0755)

	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := pb.NewFileToolsClient(conn)

	resp, err := client.ListDirectory(context.Background(), &pb.ListDirectoryRequest{
		Workspace: dir, Path: ".",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(resp.Content, "[FILE] a.txt") {
		t.Error("expected a.txt in listing")
	}
	if !strings.Contains(resp.Content, "[DIR]  sub") {
		t.Error("expected sub in listing")
	}
}

func TestBackupAndRollback(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "data.txt"), []byte("original"), 0644)

	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := pb.NewFileToolsClient(conn)

	// Backup
	backupResp, err := client.BackupFile(context.Background(), &pb.BackupFileRequest{
		Workspace: dir, Filename: "data.txt",
	})
	if err != nil {
		t.Fatal(err)
	}
	if backupResp.BackupPath == "" {
		t.Error("expected backup path")
	}

	// Modify
	os.WriteFile(filepath.Join(dir, "data.txt"), []byte("modified"), 0644)

	// Rollback
	rollbackResp, err := client.RollbackFile(context.Background(), &pb.RollbackFileRequest{
		Workspace: dir, Filename: "data.txt", BackupIndex: -1,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(rollbackResp.Message, "成功回滚") {
		t.Errorf("expected success: %s", rollbackResp.Message)
	}
}

func TestReadFunction(t *testing.T) {
	dir := t.TempDir()
	content := `def hello():
    return "world"
`
	os.WriteFile(filepath.Join(dir, "sample.py"), []byte(content), 0644)

	conn, cleanup := startTestServer(t)
	defer cleanup()
	client := pb.NewFileToolsClient(conn)

	resp, err := client.ReadFunction(context.Background(), &pb.ReadFunctionRequest{
		Workspace: dir, Filename: "sample.py", FunctionName: "hello",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(resp.Content, `return "world"`) {
		t.Error("expected function source")
	}
}
