package filetools

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// GetSafeFilepath validates that the target path is within the workspace
// and does not traverse through any symlinks.
func GetSafeFilepath(workspace, filename string) (string, error) {
	workspaceAbs, err := filepath.Abs(workspace)
	if err != nil {
		return "", fmt.Errorf("cannot resolve workspace: %w", err)
	}
	// Resolve any symlinks in the workspace itself (e.g. /var -> /private/var on macOS).
	workspaceAbs, err = filepath.EvalSymlinks(workspaceAbs)
	if err != nil {
		return "", fmt.Errorf("cannot resolve workspace: %w", err)
	}

	normalized := strings.ReplaceAll(filename, "\\", "/")

	// Build the full target path.
	var targetPath string
	if filepath.IsAbs(normalized) {
		targetPath = normalized
	} else {
		targetPath = filepath.Join(workspaceAbs, normalized)
	}

	// Walk each component of the path relative to the workspace to detect symlinks.
	// First, strip the workspace prefix to get the relative portion.
	relToWorkspace, err := filepath.Rel(workspaceAbs, targetPath)
	if err != nil {
		return "", fmt.Errorf("安全拦截：禁止访问工作区之外的路径 -> %s", filename)
	}
	if strings.HasPrefix(relToWorkspace, "..") {
		return "", fmt.Errorf("安全拦截：禁止访问工作区之外的路径 -> %s", filename)
	}

	// Walk each path component, checking for symlinks at each step.
	current := workspaceAbs
	parts := strings.Split(filepath.ToSlash(relToWorkspace), "/")
	for _, part := range parts {
		if part == "." || part == "" {
			continue
		}
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if err != nil {
			// Path doesn't exist yet; everything up to here is safe.
			break
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return "", fmt.Errorf("安全拦截：禁止访问工作区之外的路径 -> %s", filename)
		}
	}

	// Final absolute path with no symlink resolution on the target itself.
	targetAbs, err := filepath.Abs(targetPath)
	if err != nil {
		return "", fmt.Errorf("cannot resolve path: %w", err)
	}

	return targetAbs, nil
}
