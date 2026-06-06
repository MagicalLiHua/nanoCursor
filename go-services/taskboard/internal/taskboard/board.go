package taskboard

import (
	"fmt"
	"sync"
	"time"
)

// BoardManager manages multiple task boards by run_id.
type BoardManager struct {
	mu     sync.RWMutex
	boards map[string]*RunTaskBoard
}

// NewBoardManager creates a new board manager.
func NewBoardManager() *BoardManager {
	return &BoardManager{
		boards: make(map[string]*RunTaskBoard),
	}
}

// GetOrCreate returns an existing board or creates a new one.
func (m *BoardManager) GetOrCreate(runID, strategy string) *RunTaskBoard {
	m.mu.RLock()
	if b, ok := m.boards[runID]; ok {
		m.mu.RUnlock()
		return b
	}
	m.mu.RUnlock()

	m.mu.Lock()
	defer m.mu.Unlock()
	if b, ok := m.boards[runID]; ok {
		return b
	}
	b := NewRunTaskBoard(runID, strategy)
	m.boards[runID] = b
	return b
}

// Get returns an existing board or nil.
func (m *BoardManager) Get(runID string) *RunTaskBoard {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.boards[runID]
}

// Count returns the number of active boards.
func (m *BoardManager) Count() int {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return len(m.boards)
}

// --- Board methods ---

// Task returns a task by ID.
func (b *RunTaskBoard) Task(taskID string) *RunTask {
	for _, t := range b.Nodes {
		if t.ID == taskID {
			return t
		}
	}
	return nil
}

// AddTask adds or updates a task on the board.
func (b *RunTaskBoard) AddTask(task *RunTask, reason string) {
	existing := b.Task(task.ID)
	if existing != nil {
		existing.Type = task.Type
		existing.Title = task.Title
		existing.Goal = task.Goal
		existing.OwnerAgentID = task.OwnerAgentID
		existing.AgentRole = task.AgentRole
		existing.Dependencies = append([]string{}, task.Dependencies...)
		existing.CanParallel = task.CanParallel
		existing.WritesFiles = task.WritesFiles
		existing.ResourceLocks = append([]string{}, task.ResourceLocks...)
		existing.ToolPolicy = copyMap(task.ToolPolicy)
		existing.ContextPolicy = copyMap(task.ContextPolicy)
		existing.Acceptance = append([]AcceptanceCriterion{}, task.Acceptance...)
		b.recordChange("task_updated", map[string]interface{}{
			"task_id": task.ID, "node_id": task.ID, "reason": reason,
		})
	} else {
		b.Nodes = append(b.Nodes, task)
		b.recordChange("task_added", map[string]interface{}{
			"task_id": task.ID, "node_id": task.ID, "reason": reason,
		})
	}
	b.syncEdgesAndResources()
}

// RemoveTask removes a task and cleans up dependencies.
func (b *RunTaskBoard) RemoveTask(taskID, reason string) error {
	if b.Task(taskID) == nil {
		return fmt.Errorf("run task not found: %s", taskID)
	}
	var nodes []*RunTask
	for _, n := range b.Nodes {
		if n.ID != taskID {
			nodes = append(nodes, n)
		}
	}
	b.Nodes = nodes
	for _, n := range b.Nodes {
		n.Dependencies = removeString(n.Dependencies, taskID)
	}
	var edges []*RunEdge
	for _, e := range b.Edges {
		if e.FromNode != taskID && e.ToNode != taskID {
			edges = append(edges, e)
		}
	}
	b.Edges = edges
	var gates []*QualityGate
	for _, g := range b.Gates {
		if g.NodeID != taskID {
			gates = append(gates, g)
		}
	}
	b.Gates = gates
	b.recordChange("task_removed", map[string]interface{}{
		"task_id": taskID, "node_id": taskID, "reason": reason,
	})
	b.syncEdgesAndResources()
	return nil
}

// ApplyTaskStatus changes a task's status with cascade blocking on failure.
func (b *RunTaskBoard) ApplyTaskStatus(taskID, status string) error {
	if !ValidStatuses[status] {
		return fmt.Errorf("invalid task status: %s", status)
	}
	task := b.Task(taskID)
	if task == nil {
		return fmt.Errorf("run task not found: %s", taskID)
	}
	task.Status = status
	b.UpdatedAt = float64(time.Now().UnixNano()) / 1e9
	if status == StatusFailed {
		for _, child := range b.Nodes {
			if containsString(child.Dependencies, taskID) && (child.Status == StatusPending || child.Status == StatusReady) {
				child.Status = StatusBlocked
			}
		}
	}
	b.recordChange("task_status", map[string]interface{}{
		"node_id": taskID, "task_id": taskID, "status": status,
	})
	return nil
}

// ReadyNodes returns tasks whose dependencies are all satisfied.
func (b *RunTaskBoard) ReadyNodes() []*RunTask {
	passed := make(map[string]bool)
	for _, n := range b.Nodes {
		if n.Status == StatusPassed || n.Status == StatusSkipped {
			passed[n.ID] = true
		}
	}
	var ready []*RunTask
	for _, n := range b.Nodes {
		if n.Status != StatusPending && n.Status != StatusReady && n.Status != StatusBlocked {
			continue
		}
		allDepsPassed := true
		for _, dep := range n.Dependencies {
			if !passed[dep] {
				allDepsPassed = false
				break
			}
		}
		if allDepsPassed {
			n.Status = StatusReady
			ready = append(ready, n)
		}
	}
	return ready
}

// ConnectTasks adds a dependency from upstream to downstream.
func (b *RunTaskBoard) ConnectTasks(upstream, downstream, reason string) error {
	if b.Task(upstream) == nil {
		return fmt.Errorf("run task not found: %s", upstream)
	}
	target := b.Task(downstream)
	if target == nil {
		return fmt.Errorf("run task not found: %s", downstream)
	}
	if !containsString(target.Dependencies, upstream) {
		target.Dependencies = append(target.Dependencies, upstream)
	}
	b.syncEdgesAndResources()
	b.recordChange("tasks_connected", map[string]interface{}{
		"upstream_task": upstream, "downstream_task": downstream,
		"from_node": upstream, "to_node": downstream, "reason": reason,
	})
	return nil
}

// DisconnectTasks removes a dependency from upstream to downstream.
func (b *RunTaskBoard) DisconnectTasks(upstream, downstream, reason string) error {
	target := b.Task(downstream)
	if target == nil {
		return fmt.Errorf("run task not found: %s", downstream)
	}
	target.Dependencies = removeString(target.Dependencies, upstream)
	b.syncEdgesAndResources()
	b.recordChange("tasks_disconnected", map[string]interface{}{
		"upstream_task": upstream, "downstream_task": downstream,
		"from_node": upstream, "to_node": downstream, "reason": reason,
	})
	return nil
}

// ToTaskBoard returns a loop-friendly representation.
func (b *RunTaskBoard) ToTaskBoard() map[string]interface{} {
	tasks := make([]map[string]interface{}, len(b.Nodes))
	for i, n := range b.Nodes {
		tasks[i] = map[string]interface{}{
			"id": n.ID, "kind": n.Type, "title": n.Title, "goal": n.Goal,
			"status": n.Status, "agent_role": n.AgentRole,
			"blocked_by": n.Dependencies, "can_parallel": n.CanParallel,
			"writes_files": n.WritesFiles, "resource_locks": n.ResourceLocks,
			"context_policy": n.ContextPolicy,
		}
	}
	locks := make([]map[string]interface{}, len(b.Resources))
	for i, l := range b.Resources {
		locks[i] = map[string]interface{}{"id": l.ID, "owner_node_id": l.OwnerNodeID, "status": l.Status}
	}
	return map[string]interface{}{
		"run_id": b.RunID, "conversation_id": b.ConversationID,
		"strategy": b.Strategy, "status": b.Status, "revision": b.Revision,
		"tasks": tasks, "locks": locks,
		"recent_changes": b.ChangeLog, "metadata": b.Metadata,
	}
}

// --- Internal helpers ---

func (b *RunTaskBoard) recordChange(changeType string, payload map[string]interface{}) {
	b.Revision++
	b.UpdatedAt = float64(time.Now().UnixNano()) / 1e9
	b.ChangeLog = append(b.ChangeLog, map[string]interface{}{
		"revision": b.Revision, "type": changeType,
		"timestamp": b.UpdatedAt, "payload": payload,
	})
	if len(b.ChangeLog) > 100 {
		b.ChangeLog = b.ChangeLog[len(b.ChangeLog)-100:]
	}
}

func (b *RunTaskBoard) syncEdgesAndResources() {
	b.Edges = nil
	for _, node := range b.Nodes {
		for _, dep := range node.Dependencies {
			b.Edges = append(b.Edges, &RunEdge{FromNode: dep, ToNode: node.ID, Type: "depends_on"})
		}
	}
	lockIDs := make(map[string]bool)
	for _, node := range b.Nodes {
		for _, lock := range node.ResourceLocks {
			if lock != "" {
				lockIDs[lock] = true
			}
		}
	}
	existing := make(map[string]*ResourceLock)
	for _, r := range b.Resources {
		existing[r.ID] = r
	}
	b.Resources = nil
	for id := range lockIDs {
		if r, ok := existing[id]; ok {
			b.Resources = append(b.Resources, r)
		} else {
			b.Resources = append(b.Resources, &ResourceLock{ID: id, Status: "free"})
		}
	}
}

func containsString(slice []string, s string) bool {
	for _, v := range slice {
		if v == s {
			return true
		}
	}
	return false
}

func removeString(slice []string, s string) []string {
	var result []string
	for _, v := range slice {
		if v != s {
			result = append(result, v)
		}
	}
	return result
}

func copyMap(m map[string]interface{}) map[string]interface{} {
	if m == nil {
		return nil
	}
	out := make(map[string]interface{}, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}
