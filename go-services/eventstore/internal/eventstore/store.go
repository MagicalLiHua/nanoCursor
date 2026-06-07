package eventstore

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Store manages sessions and events under a workspace.
type Store struct {
	mu               sync.RWMutex
	defaultWorkspace string
	subscribers      map[string][]chan Event
	subscriberMu     sync.RWMutex
}

// NewStore creates a new store.
func NewStore(defaultWorkspace string) *Store {
	return &Store{
		defaultWorkspace: defaultWorkspace,
		subscribers:      make(map[string][]chan Event),
	}
}

// CreateSession creates a new run session.
func (s *Store) CreateSession(threadID, prompt, workspaceDir, status, mode string) *Session {
	if workspaceDir == "" {
		workspaceDir = s.defaultWorkspace
	}
	if status == "" {
		status = "running"
	}
	if mode == "" {
		mode = "agenthub_delivery"
	}
	now := float64(time.Now().UnixMilli()) / 1000.0
	session := &Session{
		ThreadID:     threadID,
		WorkspaceDir: absPath(workspaceDir),
		Status:       status,
		Prompt:       prompt,
		Mode:         mode,
		CreatedAt:    now,
		UpdatedAt:    now,
	}
	s.mu.Lock()
	s.saveSession(session)
	s.rememberThreadWorkspace(threadID, session.WorkspaceDir)
	s.mu.Unlock()
	return session
}

// GetSession retrieves a session by thread ID.
func (s *Store) GetSession(threadID, workspaceDir string) *Session {
	if workspaceDir == "" {
		workspaceDir = s.defaultWorkspace
	}
	path := s.sessionPath(threadID, workspaceDir)
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var session Session
	if err := json.Unmarshal(data, &session); err != nil {
		return nil
	}
	return &session
}

// UpdateSession updates a session's fields.
func (s *Store) UpdateSession(threadID, workspaceDir string, changes map[string]string) *Session {
	if workspaceDir == "" {
		workspaceDir = s.defaultWorkspace
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	session := s.GetSession(threadID, workspaceDir)
	if session == nil {
		return nil
	}
	if v, ok := changes["status"]; ok {
		session.Status = v
	}
	if v, ok := changes["prompt"]; ok {
		session.Prompt = v
	}
	if v, ok := changes["mode"]; ok {
		session.Mode = v
	}
	if v, ok := changes["workspace_dir"]; ok {
		session.WorkspaceDir = absPath(v)
	}
	session.UpdatedAt = float64(time.Now().UnixMilli()) / 1000.0
	s.saveSession(session)
	return session
}

// AppendEvent appends an event to the JSONL file and notifies subscribers.
func (s *Store) AppendEvent(threadID, eventType, title, content, agent, payloadJSON, workspaceDir string) *Event {
	if workspaceDir == "" {
		workspaceDir = s.defaultWorkspace
	}
	if agent == "" {
		agent = "lead"
	}
	event := &Event{
		ID:          fmt.Sprintf("evt_%d_%s", time.Now().UnixNano(), threadID[:min(8, len(threadID))]),
		ThreadID:    threadID,
		Type:        eventType,
		Timestamp:   float64(time.Now().UnixMilli()) / 1000.0,
		Agent:       agent,
		Title:       title,
		Content:     content,
		PayloadJSON: payloadJSON,
	}
	s.mu.Lock()
	eventsPath := s.eventsPath(threadID, workspaceDir)
	_ = os.MkdirAll(filepath.Dir(eventsPath), 0o755)
	f, err := os.OpenFile(eventsPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err == nil {
		line, _ := json.Marshal(event)
		_, _ = f.Write(append(line, '\n'))
		_ = f.Close()
	}
	s.mu.Unlock()

	s.notifySubscribers(threadID, event)
	return event
}

// ListEvents returns events after the given cursor position.
func (s *Store) ListEvents(threadID, workspaceDir string, after int) []*Event {
	if workspaceDir == "" {
		workspaceDir = s.defaultWorkspace
	}
	path := s.eventsPath(threadID, workspaceDir)
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	var events []*Event
	for i, line := range lines {
		if i < after || line == "" {
			continue
		}
		var ev Event
		if err := json.Unmarshal([]byte(line), &ev); err == nil {
			events = append(events, &ev)
		}
	}
	return events
}

// CountEvents returns the number of events for a thread.
func (s *Store) CountEvents(threadID, workspaceDir string) int {
	if workspaceDir == "" {
		workspaceDir = s.defaultWorkspace
	}
	path := s.eventsPath(threadID, workspaceDir)
	data, err := os.ReadFile(path)
	if err != nil {
		return 0
	}
	count := 0
	for _, line := range strings.Split(string(data), "\n") {
		if line != "" {
			count++
		}
	}
	return count
}

// WorkspaceForThread returns the workspace directory for a thread ID.
func (s *Store) WorkspaceForThread(threadID string) (string, bool) {
	path := s.threadWorkspaceIndexPath()
	data, err := os.ReadFile(path)
	if err != nil {
		return "", false
	}
	var index map[string]string
	if err := json.Unmarshal(data, &index); err != nil {
		return "", false
	}
	ws, ok := index[threadID]
	return ws, ok
}

// Subscribe creates a channel that receives events for a thread.
func (s *Store) Subscribe(threadID string) chan Event {
	ch := make(chan Event, 64)
	s.subscriberMu.Lock()
	s.subscribers[threadID] = append(s.subscribers[threadID], ch)
	s.subscriberMu.Unlock()
	return ch
}

// Unsubscribe removes a subscriber channel.
func (s *Store) Unsubscribe(threadID string, ch chan Event) {
	s.subscriberMu.Lock()
	defer s.subscriberMu.Unlock()
	subs := s.subscribers[threadID]
	for i, sub := range subs {
		if sub == ch {
			s.subscribers[threadID] = append(subs[:i], subs[i+1:]...)
			close(ch)
			break
		}
	}
	if len(s.subscribers[threadID]) == 0 {
		delete(s.subscribers, threadID)
	}
}

func (s *Store) notifySubscribers(threadID string, event *Event) {
	s.subscriberMu.RLock()
	subs := s.subscribers[threadID]
	s.subscriberMu.RUnlock()
	for _, ch := range subs {
		select {
		case ch <- *event:
		default:
		}
	}
}

func (s *Store) saveSession(session *Session) {
	path := s.sessionPath(session.ThreadID, session.WorkspaceDir)
	_ = os.MkdirAll(filepath.Dir(path), 0o755)
	data, _ := json.MarshalIndent(session, "", "  ")
	tmp := path + ".tmp"
	_ = os.WriteFile(tmp, data, 0o644)
	_ = os.Rename(tmp, path)
}

func (s *Store) rememberThreadWorkspace(threadID, workspaceDir string) {
	path := s.threadWorkspaceIndexPath()
	_ = os.MkdirAll(filepath.Dir(path), 0o755)
	var index map[string]string
	data, err := os.ReadFile(path)
	if err != nil {
		index = make(map[string]string)
	} else {
		_ = json.Unmarshal(data, &index)
		if index == nil {
			index = make(map[string]string)
		}
	}
	index[threadID] = workspaceDir
	out, _ := json.MarshalIndent(index, "", "  ")
	tmp := path + ".tmp"
	_ = os.WriteFile(tmp, out, 0o644)
	_ = os.Rename(tmp, path)
}

func (s *Store) sessionPath(threadID, workspaceDir string) string {
	safeID := strings.ReplaceAll(strings.ReplaceAll(threadID, "/", "_"), "\\", "_")
	return filepath.Join(absPath(workspaceDir), ".nanocursor", "runs", safeID, "session.json")
}

func (s *Store) eventsPath(threadID, workspaceDir string) string {
	safeID := strings.ReplaceAll(strings.ReplaceAll(threadID, "/", "_"), "\\", "_")
	return filepath.Join(absPath(workspaceDir), ".nanocursor", "runs", safeID, "events.jsonl")
}

func (s *Store) threadWorkspaceIndexPath() string {
	return filepath.Join(absPath(s.defaultWorkspace), ".nanocursor", "thread_workspaces.json")
}

func absPath(p string) string {
	a, err := filepath.Abs(p)
	if err != nil {
		return p
	}
	return a
}
