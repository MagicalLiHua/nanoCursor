package supervisor

import (
	"context"
	"path/filepath"
	"testing"
)

func TestLocalSandboxAdapterResolvesWorkspaceCwd(t *testing.T) {
	workspace := t.TempDir()
	cwd := filepath.Join(workspace, "src")
	adapter := LocalSandboxAdapter{}

	session, err := adapter.Prepare(context.Background(), SandboxSpec{
		WorkspaceDir: workspace,
		Cwd:          cwd,
	})
	if err != nil {
		t.Fatal(err)
	}
	if session.Cwd != cwd {
		t.Fatalf("expected cwd %q, got %q", cwd, session.Cwd)
	}
	if session.Metadata["workspace_dir"] == "" {
		t.Fatalf("expected workspace metadata, got %#v", session.Metadata)
	}
}

func TestLocalSandboxAdapterRejectsEscapedCwd(t *testing.T) {
	workspace := t.TempDir()
	adapter := LocalSandboxAdapter{}

	_, err := adapter.Prepare(context.Background(), SandboxSpec{
		WorkspaceDir: workspace,
		Cwd:          filepath.Dir(workspace),
	})
	if err == nil {
		t.Fatal("expected escaped cwd rejection")
	}
}
