package tools

import "time"

type ToolEvent struct {
	ID        string         `json:"id"`
	Ts        time.Time      `json:"ts"`
	RunID     string         `json:"run_id,omitempty"`
	ToolRunID string         `json:"tool_run_id"`
	Type      string         `json:"type"`
	Payload   map[string]any `json:"payload"`
}

type ToolRun struct {
	ToolRunID       string       `json:"tool_run_id"`
	Status          string       `json:"status"`
	Backend         string       `json:"backend"`
	Tool            string       `json:"tool"`
	Command         string       `json:"command"`
	Cwd             string       `json:"cwd"`
	ExitCode        int          `json:"exit_code"`
	Stdout          string       `json:"stdout"`
	Stderr          string       `json:"stderr"`
	StdoutTruncated bool         `json:"stdout_truncated"`
	StderrTruncated bool         `json:"stderr_truncated"`
	DurationMS      int64        `json:"duration_ms"`
	TimedOut        bool         `json:"timed_out"`
	ErrorCode       string       `json:"error_code,omitempty"`
	Message         string       `json:"message,omitempty"`
	Evidence        ToolEvidence `json:"evidence"`
	events          []ToolEvent
}

type ToolEvidence struct {
	Kind      string   `json:"kind"`
	Summary   string   `json:"summary"`
	Artifacts []string `json:"artifacts"`
}
