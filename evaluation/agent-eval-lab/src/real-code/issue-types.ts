import type { TraceEvent } from "../types.ts";
import type { IssueFrozenManifest } from "./issue-manifest.ts";
import type { ProcessResult, RealCodeDifficulty, RealCodePreflightResult, RealCodeTerminationReason } from "./types.ts";

export type IssueTaskSplit =
	| "development"
	| "evaluation-core"
	| "evaluation-expansion-a"
	| "evaluation-expansion-b"
	| "regression";
export type IssueNewTestPathMode = "path" | "django-label";
export type IssueTestPathMode = "tests-segment" | "tests-root" | "testing-root" | "root-test-files";
export type IssueOutcomeStatus = "COMPLETED" | "PARTIAL" | "INVALID" | "INFRA_BLOCKED" | "NEEDS_REVIEW";

export interface IssueTask {
	id: string;
	title: string;
	instanceId: string;
	repository: string;
	baseCommit: string;
	image: string;
	difficulty: RealCodeDifficulty;
	split: IssueTaskSplit;
	sourceTaskId: string;
	issue: string;
	prompt: string;
	goldPatchPath: string;
	upstreamTestPatchPath: string;
	hiddenTestCommand: string;
	regressionCommand: string;
	newTestCommandPrefix: string;
	newTestPathMode: IssueNewTestPathMode;
	testPathMode: IssueTestPathMode;
	toolSmokeArgv?: string[];
}

export interface IssueToolSmokeCheck {
	id: string;
	passed: boolean;
	message: string;
}

export interface IssueToolSmokeResult {
	taskId: string;
	instanceId: string;
	image: string;
	baseCommit: string;
	passed: boolean;
	checks: IssueToolSmokeCheck[];
	commandRun?: ProcessResult;
}

export interface IssueChangedFileArtifact {
	path: string;
	status: string;
	bytes: number;
	sha256: string;
	content: string;
}

export interface IssueGradeCheck {
	id: string;
	passed: boolean;
	message: string;
}

export interface IssueGrade {
	passed: boolean;
	checks: IssueGradeCheck[];
	repositoryStatus: string;
	finalPatch: string;
	changedFiles: IssueChangedFileArtifact[];
	forbiddenChanges: string[];
	evaluatorSetupRun: ProcessResult;
	agentTestsRun: ProcessResult;
	targetRun: ProcessResult;
	regressionRun: ProcessResult;
}

export interface IssueEvalResult {
	runId: string;
	taskId: string;
	instanceId: string;
	trialIndex: number;
	attemptIndex: number;
	runtime: string;
	model: string;
	startedAt: string;
	finishedAt: string;
	passed: boolean;
	outcomeStatus: IssueOutcomeStatus;
	terminationReason: RealCodeTerminationReason;
	providerRetryEligible: boolean;
	manifest: IssueFrozenManifest;
	budget: {
		maxTurns: number;
		maxWallTimeMs: number;
		turnsUsed: number;
	};
	finalResponse: string;
	trace: TraceEvent[];
	grade: IssueGrade;
	runError?: string;
}

export interface IssueRunOptions {
	dockerHost?: string;
	trialIndex?: number;
	attemptIndex?: number;
	commandTimeoutMs?: number;
	maxTurns?: number;
	maxWallTimeMs?: number;
	runId?: string;
}

export type IssuePreflightResult = RealCodePreflightResult;
