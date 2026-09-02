export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type PlanStepStatus = "pending" | "in_progress" | "completed" | "blocked";

export interface PlanStep {
	id: string;
	title: string;
	status: PlanStepStatus;
	note?: string;
}

export interface PlanSnapshot {
	objective: string;
	revision: number;
	steps: PlanStep[];
	updatedAt: string;
}

export interface Contact {
	id: string;
	name: string;
	email: string;
	tags: string[];
}

export interface CalendarEvent {
	id: string;
	title: string;
	start: string;
	end: string;
	attendeeEmails: string[];
	location?: string;
}

export interface Note {
	id: string;
	title: string;
	body: string;
	createdAt: string;
}

export interface Notification {
	id: string;
	recipient: string;
	message: string;
	createdAt: string;
}

export interface WeatherForecast {
	location: string;
	date: string;
	condition: string;
	temperatureC: number;
}

export interface RequirementThreshold {
	metric: string;
	value: number;
	unit: string;
}

export interface Requirement {
	id: string;
	title: string;
	description: string;
	acceptanceCriteria: string[];
	thresholds?: RequirementThreshold[];
}

export type TestRunStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
export type TestCaseStatus = "PASSED" | "FAILED";

export interface TestCaseResult {
	caseId: string;
	status: TestCaseStatus;
	expected: string;
	actual: string;
	errorSignature?: string;
	traceId?: string;
}

export interface TestRun {
	id: string;
	buildId: string;
	status: TestRunStatus;
	requestId: string;
	caseIds: string[];
	results: TestCaseResult[];
	summary?: { passed: number; failed: number };
	createdAt: string;
}

export interface ExecutionTrace {
	id: string;
	caseId: string;
	finalResponse: string;
	toolEvents: Array<{
		toolName: string;
		status: "success" | "error";
		details: string;
	}>;
	finalState: string;
}

export interface IssueEvidence {
	runId: string;
	caseId: string;
	traceId: string;
}

export interface Issue {
	id: string;
	signature: string;
	title: string;
	status: "OPEN" | "CLOSED";
	evidence: IssueEvidence[];
	labels: string[];
}

export interface ReportCategory {
	signature: string;
	count: number;
}

export type ReportConclusion = "PASSED" | "FAILED" | "INCONCLUSIVE" | "PARTIAL" | "REGRESSION_FOUND";

export interface TestReport {
	id: string;
	title: string;
	buildId: string;
	runIds: string[];
	conclusion: ReportConclusion;
	summary: string;
	passed?: number;
	failed?: number;
	fixedCaseIds?: string[];
	regressedCaseIds?: string[];
	categories?: ReportCategory[];
	caseIds?: string[];
	traceIds?: string[];
	createdAt: string;
}

export interface WorldState {
	now: string;
	contacts: Contact[];
	calendar: CalendarEvent[];
	notes: Note[];
	notifications: Notification[];
	weather: WeatherForecast[];
	requirements: Requirement[];
	testRuns: TestRun[];
	executionTraces: ExecutionTrace[];
	issues: Issue[];
	reports: TestReport[];
}

export type FaultMode =
	| "error"
	| "timeout"
	| "timeout_after_commit"
	| "empty_result"
	| "malformed_result"
	| "permission_denied"
	| "rate_limited";

export interface FaultRule {
	toolName: string;
	onCall: number;
	mode: FaultMode;
	message?: string;
}

export interface ToolInvocation {
	toolCallId: string;
	toolName: string;
	args: JsonValue;
	callIndex: number;
}

export interface PolicyDecision {
	allowed: boolean;
	ruleId: string;
	reason: string;
}

export interface TraceEvent {
	sequence: number;
	timestamp: string;
	type: string;
	payload: JsonValue;
}

export interface TaskExpectation {
	requiredTools: string[];
	allowedTools?: string[];
	forbiddenTools?: string[];
	minimumToolCalls?: Record<string, number>;
	maximumToolCalls?: Record<string, number>;
	requirePlan?: boolean;
	requireCompletedPlan?: boolean;
	allowBlockedPlanSteps?: boolean;
	calendarTitles?: string[];
	noteTitles?: string[];
	notificationRecipients?: string[];
	calendarEvents?: Array<Omit<CalendarEvent, "id">>;
	notes?: Array<Pick<Note, "title" | "body">>;
	notifications?: Array<Pick<Notification, "recipient" | "message">>;
	testRuns?: Array<{
		buildId: string;
		requestId?: string;
		status?: TestRunStatus;
		caseIds?: string[];
	}>;
	issues?: Array<{
		signature: string;
		evidenceIncludes?: IssueEvidence[];
		labelsInclude?: string[];
	}>;
	reports?: Array<{
		buildId: string;
		runIds?: string[];
		conclusion?: ReportConclusion;
		passed?: number;
		failed?: number;
		fixedCaseIds?: string[];
		regressedCaseIds?: string[];
		categories?: ReportCategory[];
		caseIds?: string[];
		traceIds?: string[];
	}>;
	forbiddenPersistedText?: string[];
	finalResponseIncludes?: string[];
	finalResponseAnyOf?: string[];
	unchangedCollections?: Array<"calendar" | "notes" | "notifications" | "testRuns" | "issues" | "reports">;
	collectionSizeDeltas?: Partial<
		Record<"calendar" | "notes" | "notifications" | "testRuns" | "issues" | "reports", number>
	>;
	expectPolicyBlocks?: number;
}

export interface ScriptedToolCall {
	tool: string;
	args: { [key: string]: JsonValue };
}

export interface ScriptedTurn {
	calls?: ScriptedToolCall[];
	text?: string;
}

export type BenchmarkSplit = "development" | "regression" | "final-test";
export type BenchmarkDifficulty = "basic" | "composite" | "hard";

export interface TaskAuthorization {
	allowedWriteTools: string[];
}

export interface BenchmarkTaskMetadata {
	dataset: string;
	version: string;
	split: BenchmarkSplit;
	difficulty: BenchmarkDifficulty;
	scenario: string;
	capabilities: string[];
	expectedToolCalls: { min: number; max: number };
}

export interface EvalTask {
	id: string;
	title: string;
	category: "planning" | "calendar" | "notes" | "notification" | "recovery" | "policy" | "hard" | "qa";
	prompt: string;
	followUpPrompts?: string[];
	initialWorld: WorldState;
	faults?: FaultRule[];
	script: ScriptedTurn[];
	expect: TaskExpectation;
	requiresModelReview?: boolean;
	highRisk?: boolean;
	authorization?: TaskAuthorization;
	benchmark?: BenchmarkTaskMetadata;
}

export interface EvalMetrics {
	taskSuccess: boolean;
	planCreated: boolean;
	planCompletionRate: number;
	requiredToolRecall: number;
	toolPrecision: number;
	policyBlocks: number;
	toolErrors: number;
	recoveredAfterError: boolean;
	duplicateSideEffects: number;
	turns: number;
	toolCalls: number;
	inputTokens: number;
	outputTokens: number;
	totalTokens: number;
	durationMs: number;
}

export interface HardEvaluationCheck {
	check_id: string;
	passed: boolean;
	message: string;
	evidence_ids: string[];
}

export interface HardEvaluationResult {
	passed: boolean;
	checks: HardEvaluationCheck[];
}

export interface ModelReviewScores {
	factual_correctness: number;
	evidence_completeness: number;
	task_completeness: number;
	recommendation_actionability: number;
	uncertainty_handling: number;
}

export interface ModelReviewResult {
	scores: ModelReviewScores;
	evidence_ids: string[];
	deductions: string[];
	confidence: number;
}

export interface LayeredDecision {
	status: "PASS" | "FAIL" | "REVIEW";
	hard_checks: HardEvaluationCheck[];
	model_review: ModelReviewResult | null;
	review_reasons: string[];
}

export interface EvalResult {
	taskId: string;
	trialIndex: number;
	runtime: string;
	model: string;
	policyProfile: string;
	passed: boolean;
	metrics: EvalMetrics;
	failures: string[];
	finalWorld: WorldState;
	finalPlan?: PlanSnapshot;
	trace: TraceEvent[];
	hardEvaluation?: HardEvaluationResult;
	layeredDecision?: LayeredDecision;
}
