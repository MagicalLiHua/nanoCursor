package policy

type RiskLevel string

const (
	RiskLow      RiskLevel = "low"
	RiskMedium   RiskLevel = "medium"
	RiskHigh     RiskLevel = "high"
	RiskCritical RiskLevel = "critical"
)

type Decision string

const (
	DecisionAllow           Decision = "allow"
	DecisionDeny            Decision = "deny"
	DecisionRequireApproval Decision = "require_approval"
)

type ToolDecision struct {
	Decision        Decision
	Reason          string
	RiskLevel       RiskLevel
	PermissionLevel string
}

type ActionDecision struct {
	Decision        Decision
	Reason          string
	RiskLevel       RiskLevel
	PermissionLevel string
	CommandType     string
}

type RunPolicy struct {
	RunID                string
	ConsecutiveFailures  int
	ConsecutiveSuccesses int
	TotalToolCalls       int
	TotalFailures        int
	CurrentRiskLevel     RiskLevel
	Escalated            bool
	BudgetBoosted        bool
}
