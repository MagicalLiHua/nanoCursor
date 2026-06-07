package eventstore

// Session represents a run session.
type Session struct {
	ThreadID     string  `json:"thread_id"`
	WorkspaceDir string  `json:"workspace_dir"`
	Status       string  `json:"status"`
	Prompt       string  `json:"prompt"`
	Mode         string  `json:"mode"`
	CreatedAt    float64 `json:"created_at"`
	UpdatedAt    float64 `json:"updated_at"`
}

// Event represents a single event in a run.
type Event struct {
	ID          string  `json:"id"`
	ThreadID    string  `json:"thread_id"`
	Type        string  `json:"type"`
	Timestamp   float64 `json:"timestamp"`
	Agent       string  `json:"agent"`
	Title       string  `json:"title"`
	Content     string  `json:"content"`
	PayloadJSON string  `json:"payload_json"`
}
