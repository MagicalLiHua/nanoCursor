package taskboard

import "time"

// Node statuses
const (
	StatusPending   = "pending"
	StatusReady     = "ready"
	StatusRunning   = "running"
	StatusBlocked   = "blocked"
	StatusPassed    = "passed"
	StatusFailed    = "failed"
	StatusSkipped   = "skipped"
	StatusCancelled = "cancelled"
)

var ValidStatuses = map[string]bool{
	StatusPending: true, StatusReady: true, StatusRunning: true,
	StatusBlocked: true, StatusPassed: true, StatusFailed: true,
	StatusSkipped: true, StatusCancelled: true,
}

// Board statuses
const (
	BoardCreated   = "created"
	BoardRunning   = "running"
	BoardPaused    = "paused"
	BoardCompleted = "completed"
	BoardFailed    = "failed"
	BoardCancelled = "cancelled"
)

// AcceptanceCriterion defines a task acceptance criterion.
type AcceptanceCriterion struct {
	ID          string `json:"id"`
	Description string `json:"description"`
	Required    bool   `json:"required"`
}

// RetryPolicy defines retry behavior for a task.
type RetryPolicy struct {
	MaxRetries   int    `json:"max_retries"`
	RetryCount   int    `json:"retry_count"`
	FallbackNode string `json:"fallback_node,omitempty"`
}

// RunTask represents a single task node on the board.
type RunTask struct {
	ID            string                   `json:"id"`
	Type          string                   `json:"type"`
	Title         string                   `json:"title"`
	Goal          string                   `json:"goal,omitempty"`
	OwnerAgentID  string                   `json:"owner_agent_id,omitempty"`
	AgentRole     string                   `json:"agent_role"`
	Status        string                   `json:"status"`
	Dependencies  []string                 `json:"dependencies,omitempty"`
	CanParallel   bool                     `json:"can_parallel,omitempty"`
	WritesFiles   bool                     `json:"writes_files,omitempty"`
	ResourceLocks []string                 `json:"resource_locks,omitempty"`
	ToolPolicy    map[string]interface{}   `json:"tool_policy,omitempty"`
	ContextPolicy map[string]interface{}   `json:"context_policy,omitempty"`
	Acceptance    []AcceptanceCriterion    `json:"acceptance,omitempty"`
	Outputs       []map[string]interface{} `json:"outputs,omitempty"`
	Evidence      []map[string]interface{} `json:"evidence,omitempty"`
	RetryPolicy   RetryPolicy              `json:"retry_policy,omitempty"`
}

// RunEdge represents a dependency edge between tasks.
type RunEdge struct {
	FromNode  string `json:"from_node"`
	ToNode    string `json:"to_node"`
	Type      string `json:"type"`
	Condition string `json:"condition,omitempty"`
}

// ResourceLock represents a resource lock.
type ResourceLock struct {
	ID          string `json:"id"`
	OwnerNodeID string `json:"owner_node_id,omitempty"`
	Status      string `json:"status"`
}

// QualityGate represents a quality gate for a task.
type QualityGate struct {
	ID     string `json:"id"`
	NodeID string `json:"node_id"`
	Title  string `json:"title"`
	Status string `json:"status"`
}

// RunTaskBoard is the main task board structure.
type RunTaskBoard struct {
	RunID          string                   `json:"run_id"`
	ConversationID string                   `json:"conversation_id,omitempty"`
	Strategy       string                   `json:"strategy"`
	Status         string                   `json:"status"`
	Nodes          []*RunTask               `json:"nodes,omitempty"`
	Edges          []*RunEdge               `json:"edges,omitempty"`
	Resources      []*ResourceLock          `json:"resources,omitempty"`
	Gates          []*QualityGate           `json:"gates,omitempty"`
	Revision       int                      `json:"revision"`
	ChangeLog      []map[string]interface{} `json:"change_log,omitempty"`
	Metadata       map[string]interface{}   `json:"metadata,omitempty"`
	CreatedAt      float64                  `json:"created_at"`
	UpdatedAt      float64                  `json:"updated_at"`
}

// NewRunTaskBoard creates a new task board.
func NewRunTaskBoard(runID, strategy string) *RunTaskBoard {
	now := float64(time.Now().UnixNano()) / 1e9
	return &RunTaskBoard{
		RunID:     runID,
		Strategy:  strategy,
		Status:    BoardCreated,
		Revision:  1,
		Metadata:  make(map[string]interface{}),
		CreatedAt: now,
		UpdatedAt: now,
	}
}
