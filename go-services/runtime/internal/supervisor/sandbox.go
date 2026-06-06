package supervisor

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
)

type SandboxSpec struct {
	Mode         string
	WorkspaceDir string
	Cwd          string
}

type SandboxSession struct {
	Cwd      string
	Metadata map[string]any
	Cleanup  func() error
}

type SandboxAdapter interface {
	Prepare(context.Context, SandboxSpec) (SandboxSession, error)
}

type LocalSandboxAdapter struct{}

func (LocalSandboxAdapter) Prepare(ctx context.Context, spec SandboxSpec) (SandboxSession, error) {
	select {
	case <-ctx.Done():
		return SandboxSession{}, ctx.Err()
	default:
	}
	workspace := spec.WorkspaceDir
	cwd := spec.Cwd
	if workspace == "" {
		workspace = cwd
	}
	if workspace == "" {
		return SandboxSession{}, errors.New("workspace_dir or cwd is required")
	}
	workspaceAbs, err := filepath.Abs(workspace)
	if err != nil {
		return SandboxSession{}, err
	}
	cwdAbs := workspaceAbs
	if cwd != "" {
		cwdAbs, err = filepath.Abs(cwd)
		if err != nil {
			return SandboxSession{}, err
		}
	}
	if !isWithin(workspaceAbs, cwdAbs) {
		return SandboxSession{}, errors.New("cwd escapes workspace")
	}
	return SandboxSession{
		Cwd: cwdAbs,
		Metadata: map[string]any{
			"mode":          "local",
			"workspace_dir": workspaceAbs,
		},
		Cleanup: func() error { return nil },
	}, nil
}

func isWithin(root string, target string) bool {
	rel, err := filepath.Rel(root, target)
	if err != nil {
		return false
	}
	return rel == "." || (rel != "" && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)))
}
