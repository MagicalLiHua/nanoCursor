import type { PlanSnapshot, TraceEvent } from "../types.ts";

export type RealCodeDifficulty = "basic" | "composite" | "hard";
export type RealCodeTaskType = "test-generation" | "patch-verification" | "failure-analysis";
export type RealCodeSplit = "development" | "final-test";
export type RealCodeMode = "regression" | "discovery" | "agent-task";
export type AgentTaskOutcomeStatus = "COMPLETED" | "PARTIAL" | "INVALID" | "INFRA_BLOCKED";
export type RealCodeTerminationReason =
	| "completed"
	| "turn-limit"
	| "wall-time-limit"
	| "output-limit"
	| "runtime-error";

export interface RealCodeTask {
	id: string;
	title: string;
	instanceId: string;
	repository: string;
	baseCommit: string;
	image: string;
	difficulty: RealCodeDifficulty;
	taskType: RealCodeTaskType;
	split: RealCodeSplit;
	prompt: string;
	goldPatchPath: string;
	upstreamTestPatchPath: string;
	generatedTestRoot: string;
	agentTestCommand: string;
	hiddenTestCommand: string;
	regressionCommand: string;
	mode?: RealCodeMode;
	sourceTaskId?: string;
}

export interface ProcessResult {
	exitCode: number;
	stdout: string;
	stderr: string;
	durationMs: number;
	timedOut: boolean;
}

export interface RealCodeGradeCheck {
	id: string;
	passed: boolean;
	message: string;
}

export interface GeneratedTestArtifact {
	path: string;
	bytes: number;
	sha256: string;
	content: string;
}

export interface RealCodeGrade {
	passed: boolean;
	checks: RealCodeGradeCheck[];
	generatedFiles: string[];
	generatedTests: GeneratedTestArtifact[];
	unexpectedChanges: string[];
	suspiciousPatterns: string[];
	buggyRun: ProcessResult;
	fixedRun: ProcessResult;
	regressionRun: ProcessResult;
}

export interface RealCodePreflightResult {
	taskId: string;
	image: string;
	baseCommit: string;
	buggyHiddenRun: ProcessResult;
	fixedHiddenRun: ProcessResult;
	regressionRun: ProcessResult;
	passed: boolean;
}

export interface RealCodeEvalResult {
	runId: string;
	taskId: string;
	evaluationMode: RealCodeMode;
	sourceTaskId?: string;
	trialIndex: number;
	runtime: string;
	model: string;
	startedAt: string;
	finishedAt: string;
	passed: boolean;
	outcomeStatus?: AgentTaskOutcomeStatus;
	terminationReason: RealCodeTerminationReason;
	budget: {
		maxTurns: number;
		maxWallTimeMs?: number;
		turnsUsed: number;
	};
	finalResponse: string;
	finalPlan?: PlanSnapshot;
	trace: TraceEvent[];
	grade: RealCodeGrade;
	runError?: string;
}

export interface RealCodeRunOptions {
	dockerHost?: string;
	trialIndex?: number;
	commandTimeoutMs?: number;
	maxTurns?: number;
	maxWallTimeMs?: number;
	runId?: string;
}
