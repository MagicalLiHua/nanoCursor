import { randomUUID } from "node:crypto";
import { Agent } from "@earendil-works/pi-agent-core";
import { finalAssistantText } from "../eval/evaluator.ts";
import type { OnlineRuntime } from "../eval/online-runner.ts";
import { TraceCollector } from "../trace/collector.ts";
import { classifyTermination } from "./agent-protocol.ts";
import { DockerRealCodeSandbox } from "./docker-sandbox.ts";
import { buildIssueFrozenManifest } from "./issue-manifest.ts";
import { isProviderInfrastructureError } from "./issue-protocol.ts";
import { issueAgentSystemPrompt } from "./issue-system-prompt.ts";
import { createIssueTools } from "./issue-tools.ts";
import type {
	IssueEvalResult,
	IssueGrade,
	IssueOutcomeStatus,
	IssuePreflightResult,
	IssueRunOptions,
	IssueTask,
	IssueToolSmokeResult,
} from "./issue-types.ts";
import type { RealCodeTerminationReason } from "./types.ts";

const DEFAULT_MAX_TURNS = 96;
const DEFAULT_MAX_WALL_TIME_MS = 20 * 60_000;

function issueOutcome(
	grade: IssueGrade,
	finalResponse: string,
	terminationReason: RealCodeTerminationReason,
): IssueOutcomeStatus {
	if (terminationReason !== "completed") return "INFRA_BLOCKED";
	if (grade.forbiddenChanges.length > 0) return "INVALID";
	if (!grade.checks.find((check) => check.id === "hidden-tests-injected")?.passed) return "INFRA_BLOCKED";
	if (grade.passed && finalResponse.trim()) return "COMPLETED";
	return "PARTIAL";
}

export async function runIssuePreflight(
	task: IssueTask,
	options: Pick<IssueRunOptions, "dockerHost" | "commandTimeoutMs"> = {},
): Promise<IssuePreflightResult> {
	const sandbox = new DockerRealCodeSandbox(task, options);
	await sandbox.start();
	try {
		return await sandbox.preflight();
	} finally {
		await sandbox.close();
	}
}

export async function runIssueToolSmoke(
	task: IssueTask,
	options: Pick<IssueRunOptions, "dockerHost" | "commandTimeoutMs"> = {},
): Promise<IssueToolSmokeResult> {
	const sandbox = new DockerRealCodeSandbox(task, options);
	await sandbox.start();
	try {
		return await sandbox.smokeIssueTools();
	} finally {
		await sandbox.close();
	}
}

export async function runIssueTask(
	task: IssueTask,
	runtime: OnlineRuntime,
	options: IssueRunOptions = {},
): Promise<IssueEvalResult> {
	const trialIndex = options.trialIndex ?? 1;
	const attemptIndex = options.attemptIndex ?? 1;
	const runId = options.runId ?? `${task.id}-t${trialIndex}-a${attemptIndex}-${randomUUID().slice(0, 8)}`;
	const maxTurns = options.maxTurns ?? DEFAULT_MAX_TURNS;
	const maxWallTimeMs = options.maxWallTimeMs ?? DEFAULT_MAX_WALL_TIME_MS;
	const commandTimeoutMs = options.commandTimeoutMs ?? 180_000;
	const startedAt = new Date().toISOString();
	const trace = new TraceCollector();
	const sandbox = new DockerRealCodeSandbox(task, { ...options, runId });
	await sandbox.start();
	let turns = 0;
	let runError: string | undefined;
	let turnLimitReached = false;
	let wallTimeLimitReached = false;
	let lastAssistantStopReason: string | undefined;
	const agent = new Agent({
		initialState: {
			systemPrompt: issueAgentSystemPrompt(),
			model: runtime.model,
			tools: createIssueTools(sandbox),
		},
		streamFn: runtime.streamFn,
		getApiKey: runtime.getApiKey,
		beforeToolCall: async (context, signal) => {
			signal?.throwIfAborted();
			trace.record("policy.decision", {
				toolCallId: context.toolCall.id,
				toolName: context.toolCall.name,
				allowed: true,
				ruleId: "issue-tool-schema-boundary",
				reason: "The tool validates repository, command, and write boundaries.",
			});
			return undefined;
		},
		afterToolCall: async (context, signal) => {
			signal?.throwIfAborted();
			trace.record("tool.finalized", {
				toolCallId: context.toolCall.id,
				toolName: context.toolCall.name,
				isError: context.isError,
				result: context.result,
			});
			return undefined;
		},
		shouldStopAfterTurn: ({ message }) => {
			turns += 1;
			const hasToolCalls = message.content.some((content) => content.type === "toolCall");
			if (turns >= maxTurns && hasToolCalls) {
				turnLimitReached = true;
				return true;
			}
			return false;
		},
		toolExecution: "sequential",
	});
	const unsubscribe = agent.subscribe((event) => {
		if (event.type === "message_end" && event.message.role === "assistant") {
			lastAssistantStopReason = event.message.stopReason;
		}
		trace.recordAgentEvent(event);
	});
	const wallTimer = setTimeout(() => {
		wallTimeLimitReached = true;
		agent.abort();
	}, maxWallTimeMs);
	try {
		await agent.prompt(task.prompt);
	} catch (error) {
		runError = error instanceof Error ? error.message : String(error);
		trace.record("run.error", { message: runError });
	} finally {
		clearTimeout(wallTimer);
		unsubscribe();
	}
	try {
		const grade = await sandbox.gradeIssue();
		const events = trace.getEvents();
		const finalResponse = finalAssistantText(events);
		const terminationReason = classifyTermination({
			wallTimeLimitReached,
			...(runError ? { runError } : {}),
			turnLimitReached,
			...(lastAssistantStopReason ? { lastAssistantStopReason } : {}),
		});
		const outcomeStatus = issueOutcome(grade, finalResponse, terminationReason);
		const effectiveModelAction =
			finalResponse.trim().length > 0 || events.some((event) => event.type === "agent.tool_execution_start");
		const providerRetryEligible =
			!effectiveModelAction && runError !== undefined && isProviderInfrastructureError(runError);
		const manifest = await buildIssueFrozenManifest(
			task,
			{ maxTurns, maxWallTimeMs, commandTimeoutMs },
			runtime.model,
		);
		return {
			runId,
			taskId: task.id,
			instanceId: task.instanceId,
			trialIndex,
			attemptIndex,
			runtime: "online-issue-agent",
			model: runtime.model.id,
			startedAt,
			finishedAt: new Date().toISOString(),
			passed: outcomeStatus === "COMPLETED",
			outcomeStatus,
			terminationReason,
			providerRetryEligible,
			manifest,
			budget: {
				maxTurns,
				maxWallTimeMs,
				turnsUsed: events.filter((event) => event.type === "agent.turn_end").length,
			},
			finalResponse,
			trace: events,
			grade,
			...(runError ? { runError } : {}),
		};
	} finally {
		await sandbox.close();
	}
}
