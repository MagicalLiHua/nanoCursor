package cron

import "time"

// CronTask represents a scheduled task.
type CronTask struct {
	ID          string    `json:"id"`
	CronExpr    string    `json:"cron_expr"`
	Prompt      string    `json:"prompt"`
	Recurring   bool      `json:"recurring"`
	Durable     bool      `json:"durable"`
	CreatedAt   time.Time `json:"created_at"`
	LastFiredAt time.Time `json:"last_fired_at,omitempty"`
}

// CronEvent represents a fired task notification.
type CronEvent struct {
	Type      string `json:"type"`
	TaskID    string `json:"task_id"`
	Prompt    string `json:"prompt"`
	Recurring bool   `json:"recurring"`
	FiredAt   int64  `json:"fired_at"`
}
