package supervisor

import "sync"

type LimitScope struct {
	Workspace string
	RunID     string
}

type LimitSnapshot struct {
	WorkspaceActive int
	RunActive       int
	MaxWorkspace    int
	MaxRun          int
}

type Limiter struct {
	mu                sync.Mutex
	activeByWorkspace map[string]int
	activeByRun       map[string]int
	maxWorkspace      int
	maxRun            int
}

type LimitSlot struct {
	limiter  *Limiter
	scope    LimitScope
	released bool
}

func NewLimiter(maxWorkspace int, maxRun int) *Limiter {
	if maxWorkspace <= 0 {
		maxWorkspace = 1
	}
	if maxRun <= 0 {
		maxRun = 1
	}
	return &Limiter{
		activeByWorkspace: map[string]int{},
		activeByRun:       map[string]int{},
		maxWorkspace:      maxWorkspace,
		maxRun:            maxRun,
	}
}

func (l *Limiter) TryAcquire(scope LimitScope) (*LimitSlot, LimitSnapshot, bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	snapshot := l.snapshotLocked(scope)
	if scope.Workspace != "" && snapshot.WorkspaceActive >= l.maxWorkspace {
		return nil, snapshot, false
	}
	if scope.RunID != "" && snapshot.RunActive >= l.maxRun {
		return nil, snapshot, false
	}
	if scope.Workspace != "" {
		l.activeByWorkspace[scope.Workspace]++
	}
	if scope.RunID != "" {
		l.activeByRun[scope.RunID]++
	}
	return &LimitSlot{limiter: l, scope: scope}, l.snapshotLocked(scope), true
}

func (l *Limiter) Snapshot(scope LimitScope) LimitSnapshot {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.snapshotLocked(scope)
}

func (s *LimitSlot) Release() {
	if s == nil || s.limiter == nil {
		return
	}
	s.limiter.mu.Lock()
	defer s.limiter.mu.Unlock()
	if s.released {
		return
	}
	if s.scope.Workspace != "" && s.limiter.activeByWorkspace[s.scope.Workspace] > 0 {
		s.limiter.activeByWorkspace[s.scope.Workspace]--
		if s.limiter.activeByWorkspace[s.scope.Workspace] == 0 {
			delete(s.limiter.activeByWorkspace, s.scope.Workspace)
		}
	}
	if s.scope.RunID != "" && s.limiter.activeByRun[s.scope.RunID] > 0 {
		s.limiter.activeByRun[s.scope.RunID]--
		if s.limiter.activeByRun[s.scope.RunID] == 0 {
			delete(s.limiter.activeByRun, s.scope.RunID)
		}
	}
	s.released = true
}

func (l *Limiter) snapshotLocked(scope LimitScope) LimitSnapshot {
	return LimitSnapshot{
		WorkspaceActive: l.activeByWorkspace[scope.Workspace],
		RunActive:       l.activeByRun[scope.RunID],
		MaxWorkspace:    l.maxWorkspace,
		MaxRun:          l.maxRun,
	}
}
