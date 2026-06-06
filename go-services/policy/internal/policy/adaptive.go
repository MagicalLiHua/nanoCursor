package policy

import (
	"fmt"
	"sync"
)

const (
	EscalationFailureThreshold = 3
	BonusBudgetSuccessStreak   = 5
	BonusBudgetFactor          = 0.2
)

// PolicyEngine manages adaptive policy state across runs.
type PolicyEngine struct {
	mu       sync.RWMutex
	policies map[string]*RunPolicy
}

// NewPolicyEngine creates a new policy engine.
func NewPolicyEngine() *PolicyEngine {
	return &PolicyEngine{
		policies: make(map[string]*RunPolicy),
	}
}

// getOrCreatePolicy returns the policy for a run, creating one if needed.
func (e *PolicyEngine) getOrCreatePolicy(runID string) *RunPolicy {
	if runID == "" {
		runID = "_default"
	}
	if p, ok := e.policies[runID]; ok {
		return p
	}
	p := &RunPolicy{
		RunID:            runID,
		CurrentRiskLevel: RiskMedium,
	}
	e.policies[runID] = p
	return p
}

// RecordResult records a tool call result and returns an adaptation event if policy changed.
func (e *PolicyEngine) RecordResult(runID, toolName string, success bool, errorMsg string) *AdaptationEvent {
	e.mu.Lock()
	defer e.mu.Unlock()

	p := e.getOrCreatePolicy(runID)
	p.TotalToolCalls++

	if success {
		p.ConsecutiveFailures = 0
		p.ConsecutiveSuccesses++
	} else {
		p.ConsecutiveSuccesses = 0
		p.ConsecutiveFailures++
		p.TotalFailures++
	}

	// Escalate after consecutive failures
	if p.ConsecutiveFailures >= EscalationFailureThreshold && !p.Escalated {
		p.Escalated = true
		p.CurrentRiskLevel = RiskHigh
		return &AdaptationEvent{
			Type:         "policy_escalated",
			Reason:       fmt.Sprintf("连续 %d 次失败，策略已升级", p.ConsecutiveFailures),
			NewRiskLevel: RiskHigh,
		}
	}

	// Boost budget after sustained success
	if p.ConsecutiveSuccesses >= BonusBudgetSuccessStreak && !p.BudgetBoosted && !p.Escalated {
		p.BudgetBoosted = true
		return &AdaptationEvent{
			Type:         "policy_relaxed",
			Reason:       fmt.Sprintf("连续 %d 次成功，策略已放松", p.ConsecutiveSuccesses),
			NewRiskLevel: RiskLow,
		}
	}

	return nil
}

// GetPolicyState returns the current policy state for a run.
func (e *PolicyEngine) GetPolicyState(runID string) *RunPolicy {
	e.mu.RLock()
	defer e.mu.RUnlock()

	return e.getOrCreatePolicy(runID)
}

// AdaptationEvent represents a policy change event.
type AdaptationEvent struct {
	Type         string
	Reason       string
	NewRiskLevel RiskLevel
}
