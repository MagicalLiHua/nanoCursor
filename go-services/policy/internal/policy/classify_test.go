package policy

import "testing"

func TestClassifyShellCommand(t *testing.T) {
	tests := []struct {
		cmd  string
		want string
	}{
		{"ls -la", "shell_safe"},
		{"cat README.md", "shell_safe"},
		{"grep -r 'TODO' src/", "shell_safe"},
		{"git status", "shell_safe"},
		{"git diff", "shell_safe"},
		{"pytest tests/", "shell_safe"},
		{"python -m pytest", "shell_safe"},
		{"npm test", "shell_safe"},
		{"npm run build", "shell_safe"},
		{"rm -rf /tmp/foo", "shell_risky"},
		{"git push origin main", "shell_risky"},
		{"pip install requests", "shell_risky"},
		{"curl https://example.com", "shell_risky"},
		{"sudo apt-get install foo", "shell_risky"},
		{"chmod 777 file", "shell_risky"},
		{"docker run ubuntu", "shell_risky"},
		{"", "shell_risky"},
		{"   ", "shell_risky"},
		{"echo hello", "shell_safe"},
		{"python script.py", "shell_safe"},
	}

	for _, tt := range tests {
		got := ClassifyShellCommand(tt.cmd)
		if got != tt.want {
			t.Errorf("ClassifyShellCommand(%q) = %q, want %q", tt.cmd, got, tt.want)
		}
	}
}

func TestClassifyToolPermission(t *testing.T) {
	tests := []struct {
		tool  string
		input string
		want  Decision
	}{
		{"read_file", "", DecisionAllow},
		{"write_file", "", DecisionAllow},
		{"delete_file", "", DecisionRequireApproval},
		{"bash", "ls -la", DecisionAllow},
		{"bash", "rm -rf /", DecisionRequireApproval},
		{"run_tests", "", DecisionAllow},
		{"unknown_tool", "", DecisionRequireApproval},
	}

	for _, tt := range tests {
		got := ClassifyToolPermission(tt.tool, tt.input)
		if got.Decision != tt.want {
			t.Errorf("ClassifyToolPermission(%q, %q) decision = %q, want %q",
				tt.tool, tt.input, got.Decision, tt.want)
		}
	}
}

func TestClassifyMCPToolPermission(t *testing.T) {
	tests := []struct {
		name string
		want string
	}{
		{"list_files", "mcp_read"},
		{"get_content", "mcp_read"},
		{"search_code", "mcp_read"},
		{"create_file", "mcp_write"},
		{"delete_item", "mcp_write"},
		{"unknown_tool_xyz", "external_risky"},
	}

	for _, tt := range tests {
		got := classifyMCPToolPermission(tt.name, "")
		if got != tt.want {
			t.Errorf("classifyMCPToolPermission(%q) = %q, want %q", tt.name, got, tt.want)
		}
	}
}
