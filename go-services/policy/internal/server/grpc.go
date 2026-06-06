package server

import (
	"context"

	"nanocursor/go-services/policy/internal/policy"
)

// PolicyServiceImpl implements the gRPC Policy service.
type PolicyServiceImpl struct {
	UnimplementedPolicyServer
	engine *policy.PolicyEngine
}

// NewPolicyServer creates a new gRPC server instance.
func NewPolicyServer() *PolicyServiceImpl {
	return &PolicyServiceImpl{
		engine: policy.NewPolicyEngine(),
	}
}

// CheckTool handles CheckTool RPC.
func (s *PolicyServiceImpl) CheckTool(ctx context.Context, req *CheckToolRequest) (*CheckToolResponse, error) {
	decision := policy.ClassifyToolPermission(req.ToolName, req.ToolInput)
	return &CheckToolResponse{
		Decision:  string(decision.Decision),
		Reason:    decision.Reason,
		RiskLevel: string(decision.RiskLevel),
	}, nil
}

// CheckAction handles CheckAction RPC.
func (s *PolicyServiceImpl) CheckAction(ctx context.Context, req *CheckActionRequest) (*CheckActionResponse, error) {
	cmdType := policy.ClassifyShellCommand(req.Command)
	riskLevel := policy.RiskLow
	decision := policy.DecisionAllow
	reason := "安全命令"

	if cmdType == "shell_risky" {
		riskLevel = policy.RiskHigh
		decision = policy.DecisionRequireApproval
		reason = "高风险命令，需要审批"
	}

	return &CheckActionResponse{
		Decision:    string(decision),
		Reason:      reason,
		RiskLevel:   string(riskLevel),
		CommandType: cmdType,
	}, nil
}

// RecordResult handles RecordResult RPC.
func (s *PolicyServiceImpl) RecordResult(ctx context.Context, req *RecordResultRequest) (*RecordResultResponse, error) {
	event := s.engine.RecordResult(req.RunId, req.ToolName, req.Success, req.ErrorMessage)
	if event == nil {
		return &RecordResultResponse{PolicyChanged: false}, nil
	}
	return &RecordResultResponse{
		PolicyChanged:    true,
		NewDecision:      string(event.NewRiskLevel),
		AdaptationReason: event.Reason,
	}, nil
}

// GetPolicyState handles GetPolicyState RPC.
func (s *PolicyServiceImpl) GetPolicyState(ctx context.Context, req *GetPolicyStateRequest) (*GetPolicyStateResponse, error) {
	p := s.engine.GetPolicyState(req.RunId)
	return &GetPolicyStateResponse{
		ConsecutiveFailures:  int32(p.ConsecutiveFailures),
		ConsecutiveSuccesses: int32(p.ConsecutiveSuccesses),
		TotalToolCalls:       int32(p.TotalToolCalls),
		TotalFailures:        int32(p.TotalFailures),
		CurrentRiskLevel:     string(p.CurrentRiskLevel),
	}, nil
}

// Health handles Health RPC.
func (s *PolicyServiceImpl) Health(ctx context.Context, req *HealthRequest) (*HealthResponse, error) {
	return &HealthResponse{
		Ok:      true,
		Service: "nanocursor-policy",
		Version: "0.1.0",
	}, nil
}
