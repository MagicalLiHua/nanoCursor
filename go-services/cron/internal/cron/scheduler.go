package cron

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Scheduler manages cron tasks and fires events.
type Scheduler struct {
	mu              sync.RWMutex
	tasks           map[string]*CronTask
	events          []CronEvent
	eventCh         chan struct{}
	persistencePath string
	running         bool
	stopCh          chan struct{}
	nextID          int
}

// NewScheduler creates a new scheduler.
func NewScheduler(persistencePath string) *Scheduler {
	return &Scheduler{
		tasks:           make(map[string]*CronTask),
		eventCh:         make(chan struct{}, 1),
		persistencePath: persistencePath,
		stopCh:          make(chan struct{}),
	}
}

// Start begins the 1-second tick loop.
func (s *Scheduler) Start() {
	s.mu.Lock()
	if s.running {
		s.mu.Unlock()
		return
	}
	s.running = true
	s.mu.Unlock()
	go s.tickLoop()
}

// Stop halts the scheduler.
func (s *Scheduler) Stop() {
	s.mu.Lock()
	if !s.running {
		s.mu.Unlock()
		return
	}
	s.running = false
	s.mu.Unlock()
	close(s.stopCh)
}

// Create adds a new task and returns its ID.
func (s *Scheduler) Create(cronExpr, prompt string, recurring, durable bool) string {
	s.mu.Lock()
	s.nextID++
	id := fmt.Sprintf("cron_%d_%d", time.Now().UnixMilli(), s.nextID)
	task := &CronTask{
		ID:        id,
		CronExpr:  cronExpr,
		Prompt:    prompt,
		Recurring: recurring,
		Durable:   durable,
		CreatedAt: time.Now(),
	}
	s.tasks[id] = task
	s.mu.Unlock()

	if durable {
		s.persist()
	}
	return id
}

// Delete removes a task by ID.
func (s *Scheduler) Delete(taskID string) bool {
	s.mu.Lock()
	task, ok := s.tasks[taskID]
	if !ok {
		s.mu.Unlock()
		return false
	}
	durable := task.Durable
	delete(s.tasks, taskID)
	s.mu.Unlock()

	if durable {
		s.persist()
	}
	return true
}

// ListAll returns all tasks.
func (s *Scheduler) ListAll() []*CronTask {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := make([]*CronTask, 0, len(s.tasks))
	for _, t := range s.tasks {
		cp := *t
		result = append(result, &cp)
	}
	return result
}

// DrainEvents blocks until events are available, then returns and clears them.
func (s *Scheduler) DrainEvents() []CronEvent {
	<-s.eventCh
	s.mu.Lock()
	events := s.events
	s.events = nil
	s.mu.Unlock()
	return events
}

// LoadFromFile loads durable tasks from the persistence file.
func (s *Scheduler) LoadFromFile() {
	data, err := os.ReadFile(s.persistencePath)
	if err != nil {
		return
	}
	var tasks []*CronTask
	if err := json.Unmarshal(data, &tasks); err != nil {
		return
	}
	s.mu.Lock()
	for _, t := range tasks {
		s.tasks[t.ID] = t
	}
	s.mu.Unlock()
}

func (s *Scheduler) tickLoop() {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-s.stopCh:
			return
		case now := <-ticker.C:
			s.checkTasks(now)
		}
	}
}

func (s *Scheduler) checkTasks(now time.Time) {
	s.mu.Lock()
	var toDelete []string
	for _, task := range s.tasks {
		if !CronMatches(task.CronExpr, now) {
			continue
		}
		if !task.LastFiredAt.IsZero() && now.Sub(task.LastFiredAt) < 60*time.Second {
			continue
		}
		task.LastFiredAt = now
		event := CronEvent{
			Type:      "cron_fired",
			TaskID:    task.ID,
			Prompt:    task.Prompt,
			Recurring: task.Recurring,
			FiredAt:   now.Unix(),
		}
		s.events = append(s.events, event)
		if !task.Recurring {
			toDelete = append(toDelete, task.ID)
		}
	}
	for _, id := range toDelete {
		delete(s.tasks, id)
	}
	hasEvents := len(s.events) > 0
	needPersist := len(toDelete) > 0
	s.mu.Unlock()

	if hasEvents {
		select {
		case s.eventCh <- struct{}{}:
		default:
		}
	}
	if needPersist {
		s.persist()
	}
}

func (s *Scheduler) persist() {
	if s.persistencePath == "" {
		return
	}
	s.mu.RLock()
	var durable []*CronTask
	for _, t := range s.tasks {
		if t.Durable {
			cp := *t
			durable = append(durable, &cp)
		}
	}
	s.mu.RUnlock()

	data, _ := json.MarshalIndent(durable, "", "  ")
	_ = os.MkdirAll(filepath.Dir(s.persistencePath), 0o755)
	tmp := s.persistencePath + ".tmp"
	_ = os.WriteFile(tmp, data, 0o644)
	_ = os.Rename(tmp, s.persistencePath)
}
