package policy

import (
	"path/filepath"
	"strings"
)

type Decision struct {
	Allowed          bool     `json:"allowed"`
	PermissionLevel  string   `json:"permission_level"`
	RequiresApproval bool     `json:"requires_approval"`
	ErrorCode        string   `json:"error_code,omitempty"`
	Message          string   `json:"message,omitempty"`
	Reasons          []string `json:"reasons"`
	WorkspaceDir     string   `json:"workspace_dir,omitempty"`
	Cwd              string   `json:"cwd,omitempty"`
}

type Input struct {
	WorkspaceDir     string
	Cwd              string
	Command          string
	PermissionLevel  string
	RequiresApproval bool
	ApprovalID       string
	ApprovalToken    string
}

var riskyPatterns = []string{
	"rm -rf",
	"sudo ",
	"shutdown",
	"reboot",
	"mkfs",
	"dd if=",
	"format c:",
	"del /f /s",
	"git reset",
	"git clean",
	"git checkout",
	"curl ",
	"wget ",
}

func Preview(input Input) Decision {
	workspace, cwd, reason, ok := normalizeWorkspace(input.WorkspaceDir, input.Cwd)
	if !ok {
		return Decision{
			Allowed:          false,
			PermissionLevel:  normalizedPermission(input),
			RequiresApproval: input.RequiresApproval,
			ErrorCode:        "workspace_boundary_violation",
			Message:          reason,
			Reasons:          []string{reason},
		}
	}

	level := normalizedPermission(input)
	reasons := []string{"cwd is inside workspace"}
	if pattern := RiskyPattern(input.Command); pattern != "" {
		reasons = append(reasons, "matched risky command pattern: "+pattern)
		if input.ApprovalToken == "" {
			return Decision{
				Allowed:          false,
				PermissionLevel:  "shell_risky",
				RequiresApproval: true,
				ErrorCode:        "approval_required",
				Message:          "shell_risky command requires approval token",
				Reasons:          reasons,
				WorkspaceDir:     workspace,
				Cwd:              cwd,
			}
		}
		if reason := ValidateApprovalToken(input, workspace); reason != "" {
			return Decision{
				Allowed:          false,
				PermissionLevel:  "shell_risky",
				RequiresApproval: true,
				ErrorCode:        "approval_invalid",
				Message:          reason,
				Reasons:          append(reasons, reason),
				WorkspaceDir:     workspace,
				Cwd:              cwd,
			}
		}
		level = "shell_risky"
	}

	if input.RequiresApproval && input.ApprovalToken == "" {
		return Decision{
			Allowed:          false,
			PermissionLevel:  level,
			RequiresApproval: true,
			ErrorCode:        "approval_required",
			Message:          "approved tool call is missing approval token",
			Reasons:          append(reasons, "approval token missing"),
			WorkspaceDir:     workspace,
			Cwd:              cwd,
		}
	}
	if input.RequiresApproval && input.ApprovalToken != "" {
		if reason := ValidateApprovalToken(input, workspace); reason != "" {
			return Decision{
				Allowed:          false,
				PermissionLevel:  level,
				RequiresApproval: true,
				ErrorCode:        "approval_invalid",
				Message:          reason,
				Reasons:          append(reasons, reason),
				WorkspaceDir:     workspace,
				Cwd:              cwd,
			}
		}
	}

	reasons = append(reasons, "command accepted by runtime policy")
	return Decision{
		Allowed:          true,
		PermissionLevel:  level,
		RequiresApproval: input.RequiresApproval || level == "shell_risky",
		Reasons:          reasons,
		WorkspaceDir:     workspace,
		Cwd:              cwd,
	}
}

func RiskyPattern(command string) string {
	lower := strings.ToLower(command)
	for _, pattern := range riskyPatterns {
		if strings.Contains(lower, pattern) {
			return pattern
		}
	}
	return ""
}

func normalizedPermission(input Input) string {
	if input.PermissionLevel != "" {
		return input.PermissionLevel
	}
	if RiskyPattern(input.Command) != "" {
		return "shell_risky"
	}
	return "shell_safe"
}

func normalizeWorkspace(workspaceDir string, cwd string) (string, string, string, bool) {
	workspace, err := filepath.Abs(workspaceDir)
	if err != nil || workspace == "" {
		return "", "", "workspace_dir is invalid", false
	}
	if cwd == "" {
		cwd = workspace
	}
	resolvedCwd, err := filepath.Abs(cwd)
	if err != nil || resolvedCwd == "" {
		return workspace, "", "cwd is invalid", false
	}
	rel, err := filepath.Rel(workspace, resolvedCwd)
	if err != nil {
		return workspace, resolvedCwd, "cwd cannot be related to workspace", false
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return workspace, resolvedCwd, "cwd is outside workspace", false
	}
	return workspace, resolvedCwd, "", true
}
