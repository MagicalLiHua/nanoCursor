package policy

import (
	"strings"
	"unicode"
)

var readOnlyTools = map[string]bool{
	"read_file": true, "read_file_range": true, "read_function": true,
	"read_class": true, "list_directory": true, "search_codebase": true,
	"project_context": true, "git_status": true, "git_diff": true,
	"task_list": true, "recall_memories": true,
}

var safeWriteTools = map[string]bool{
	"write_file": true, "edit_file": true, "task_create": true,
	"task_update": true, "add_memory": true, "spawn_agent": true,
}

var riskyWriteTools = map[string]bool{
	"delete_file": true, "move_file": true, "rollback_file": true,
	"restore_snapshot": true, "apply_patch": true,
}

var shellTools = map[string]bool{
	"bash": true, "run_bash": true, "run_tests": true,
}

var shellSafePrefixes = [][]string{
	{"ls"}, {"dir"}, {"echo"}, {"pwd"}, {"cat"}, {"type"},
	{"head"}, {"tail"}, {"grep"}, {"rg"}, {"find"},
	{"git", "status"}, {"git", "diff"},
	{"pytest"}, {"ruff"}, {"mypy"}, {"eslint"}, {"tsc", "--noEmit"},
	{"node", "--check"},
	{"python", "-m", "pytest"}, {"python3", "-m", "pytest"},
	{"python", "-m", "unittest"}, {"python3", "-m", "unittest"},
	{"python", "-m", "py_compile"}, {"python3", "-m", "py_compile"},
	{"python", "--version"}, {"python3", "--version"},
	{"npm", "test"}, {"npm", "run", "test"}, {"npm", "run", "check"},
	{"npm", "run", "lint"}, {"npm", "run", "typecheck"}, {"npm", "run", "build"},
}

var shellRiskyTokens = map[string]bool{
	"rm": true, "del": true, "rmdir": true, "mv": true, "move": true,
	"cp": true, "copy": true, "chmod": true, "chown": true, "sudo": true,
	"git": true, "pip": true, "pip3": true, "uv": true, "poetry": true,
	"npm": true, "pnpm": true, "yarn": true, "curl": true, "wget": true,
	"ssh": true, "scp": true, "docker": true, "kubectl": true,
}

var shellRiskyPatterns = []string{
	"rm -rf", "git reset", "git clean", "git checkout", "git switch",
	"git commit", "git push", "pip install", "npm install", "pnpm install",
	"yarn install", "curl ", "wget ", "http://", "https://",
	">", ">>", "| sh", "| bash",
}

var mcpReadPrefixes = []string{
	"list", "get", "read", "search", "find", "query", "fetch",
	"inspect", "describe", "resolve", "lookup",
}

var mcpWriteTokens = []string{
	"create", "update", "delete", "remove", "write", "edit",
	"mutate", "submit", "approve", "merge", "commit", "push",
	"post", "upload", "install",
}

func tokensStartWith(tokens []string, prefix []string) bool {
	if len(tokens) < len(prefix) {
		return false
	}
	for i, p := range prefix {
		if strings.ToLower(tokens[i]) != p {
			return false
		}
	}
	return true
}

func isSafePythonScript(tokens []string) bool {
	if len(tokens) < 2 {
		return false
	}
	cmd := strings.ToLower(tokens[0])
	if cmd != "python" && cmd != "python3" {
		return false
	}
	script := tokens[1]
	if strings.HasPrefix(script, "-") || !strings.HasSuffix(script, ".py") {
		return false
	}
	if strings.HasPrefix(script, "/") || strings.HasPrefix(script, "~") || strings.Contains(script, "..") {
		return false
	}
	for _, token := range tokens[2:] {
		lowered := strings.ToLower(token)
		for _, marker := range []string{";", "&&", "||", "|", ">", "<", "`", "$("} {
			if strings.Contains(lowered, marker) {
				return false
			}
		}
		if strings.HasPrefix(token, "/") || strings.HasPrefix(token, "~") || strings.Contains(token, "..") {
			return false
		}
	}
	return true
}

func stripSafeCdPrefix(tokens []string) []string {
	if len(tokens) >= 5 && strings.ToLower(tokens[0]) == "cd" && tokens[2] == "&&" {
		target := tokens[1]
		if strings.HasPrefix(target, "/") || strings.HasPrefix(target, "./") || strings.HasPrefix(target, "../") || strings.HasPrefix(target, "~") {
			return tokens[3:]
		}
	}
	return nil
}

func stripEchoFallback(tokens []string) []string {
	for i, t := range tokens {
		if t == "||" && i+1 < len(tokens) && strings.ToLower(tokens[i+1]) == "echo" {
			return tokens[:i]
		}
	}
	return tokens
}

func stripTimeoutPrefix(tokens []string) []string {
	if len(tokens) >= 3 && strings.ToLower(tokens[0]) == "timeout" {
		allDigits := true
		for _, c := range tokens[1] {
			if !unicode.IsDigit(c) {
				allDigits = false
				break
			}
		}
		if allDigits {
			return tokens[2:]
		}
	}
	return tokens
}

func splitShell(text string) []string {
	var tokens []string
	var current strings.Builder
	inSingle, inDouble := false, false

	for i := 0; i < len(text); i++ {
		ch := text[i]
		switch {
		case ch == '\'' && !inDouble:
			inSingle = !inSingle
		case ch == '"' && !inSingle:
			inDouble = !inDouble
		case ch == ' ' && !inSingle && !inDouble:
			if current.Len() > 0 {
				tokens = append(tokens, current.String())
				current.Reset()
			}
		default:
			current.WriteByte(ch)
		}
	}
	if current.Len() > 0 {
		tokens = append(tokens, current.String())
	}
	return tokens
}

func ClassifyShellCommand(command string) string {
	text := strings.TrimSpace(command)
	if text == "" {
		return "shell_risky"
	}

	tokens := splitShell(text)
	if len(tokens) == 0 {
		return "shell_risky"
	}

	var filtered []string
	for _, t := range tokens {
		if t != "2>&1" {
			filtered = append(filtered, t)
		}
	}
	tokens = stripTimeoutPrefix(stripEchoFallback(filtered))
	if len(tokens) == 0 {
		return "shell_risky"
	}

	lowered := strings.ToLower(strings.Join(tokens, " "))
	for _, pattern := range shellRiskyPatterns {
		if strings.Contains(lowered, pattern) {
			return "shell_risky"
		}
	}

	safeTail := stripSafeCdPrefix(tokens)
	if safeTail != nil {
		safeTail = stripTimeoutPrefix(safeTail)
		if isSafePythonScript(safeTail) || matchesSafePrefix(safeTail) {
			return "shell_safe"
		}
	}

	for _, t := range tokens {
		if t == ";" || t == "&&" || t == "||" || t == "|" || t == "&" {
			return "shell_risky"
		}
	}

	head := strings.ToLower(tokens[0])

	if head == "find" {
		for _, t := range tokens {
			if strings.ToLower(t) == "-delete" {
				return "shell_risky"
			}
		}
	}

	if shellRiskyTokens[head] {
		if matchesSafePrefix(tokens) || isSafePythonScript(tokens) {
			return "shell_safe"
		}
		return "shell_risky"
	}

	if matchesSafePrefix(tokens) || isSafePythonScript(tokens) {
		return "shell_safe"
	}

	return "shell_risky"
}

func matchesSafePrefix(tokens []string) bool {
	for _, prefix := range shellSafePrefixes {
		if tokensStartWith(tokens, prefix) {
			return true
		}
	}
	return false
}

func ClassifyToolPermission(toolName string, toolInput string) ToolDecision {
	name := strings.TrimSpace(toolName)

	if name == "run_tests" {
		return ToolDecision{
			Decision: DecisionAllow, Reason: "run_tests 自动放行",
			RiskLevel: RiskLow, PermissionLevel: "shell_safe",
		}
	}

	if shellTools[name] {
		cmdType := ClassifyShellCommand(toolInput)
		if cmdType == "shell_safe" {
			return ToolDecision{
				Decision: DecisionAllow, Reason: "安全 shell 命令",
				RiskLevel: RiskLow, PermissionLevel: "shell_safe",
			}
		}
		return ToolDecision{
			Decision: DecisionRequireApproval, Reason: "高风险 shell 命令",
			RiskLevel: RiskHigh, PermissionLevel: "shell_risky",
		}
	}

	if readOnlyTools[name] {
		return ToolDecision{
			Decision: DecisionAllow, Reason: "只读工具",
			RiskLevel: RiskLow, PermissionLevel: "read_only",
		}
	}

	if riskyWriteTools[name] {
		return ToolDecision{
			Decision: DecisionRequireApproval, Reason: "高风险写操作",
			RiskLevel: RiskHigh, PermissionLevel: "risky_write",
		}
	}

	if safeWriteTools[name] {
		return ToolDecision{
			Decision: DecisionAllow, Reason: "安全写操作",
			RiskLevel: RiskMedium, PermissionLevel: "safe_write",
		}
	}

	if strings.HasPrefix(name, "mcp_") || name == "mcp_call" {
		level := classifyMCPToolPermission(name, toolInput)
		if level == "mcp_read" {
			return ToolDecision{
				Decision: DecisionAllow, Reason: "MCP 只读操作",
				RiskLevel: RiskLow, PermissionLevel: "mcp_read",
			}
		}
		return ToolDecision{
			Decision: DecisionRequireApproval, Reason: "MCP 写操作",
			RiskLevel: RiskHigh, PermissionLevel: level,
		}
	}

	return ToolDecision{
		Decision: DecisionRequireApproval, Reason: "未知工具，需要审批",
		RiskLevel: RiskHigh, PermissionLevel: "external_risky",
	}
}

func classifyMCPToolPermission(toolName string, toolInput string) string {
	lowered := strings.ToLower(strings.ReplaceAll(toolName, "-", "_"))

	for _, token := range mcpWriteTokens {
		if strings.Contains(lowered, token) {
			return "mcp_write"
		}
	}
	for _, prefix := range mcpReadPrefixes {
		if strings.HasPrefix(lowered, prefix) || strings.Contains(lowered, "_"+prefix) {
			return "mcp_read"
		}
	}
	return "external_risky"
}
