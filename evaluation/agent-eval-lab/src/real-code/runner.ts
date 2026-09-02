import { randomUUID } from "node:crypto";
import { Agent } from "@earendil-works/pi-agent-core";
import { finalAssistantText } from "../eval/evaluator.ts";
import type { OnlineRuntime } from "../eval/online-runner.ts";
import { PlanStore } from "../plan/store.ts";
import { createPlanTools } from "../plan/tools.ts";
import { TraceCollector } from "../trace/collector.ts";
import { classifyTermination, realCodeRunLimits } from "./agent-protocol.ts";
import { DockerRealCodeSandbox } from "./docker-sandbox.ts";
import { realCodeSystemPrompt } from "./system-prompt.ts";
import { RealCodeToolPolicy } from "./tool-policy.ts";
import { createRealCodeTools } from "./tools.ts";
import type {
	AgentTaskOutcomeStatus,
	RealCodeEvalResult,
	RealCodeGrade,
	RealCodePreflightResult,
	RealCodeRunOptions,
	RealCodeTask,
	RealCodeTerminationReason,
} from "./types.ts";

function agentTaskOutcome(
	grade: RealCodeGrade,
	finalResponse: string,
	terminationReason: RealCodeTerminationReason,
): AgentTaskOutcomeStatus {
	if (terminationReason !== "completed") return "INFRA_BLOCKED";
	if (grade.passed && finalResponse.trim()) return "COMPLETED";
	const validArtifact =
		grade.generatedFiles.length > 0 &&
		grade.checks.find((check) => check.id === "product-source-unchanged")?.passed === true &&
		grade.checks.find((check) => check.id === "anti-cheat-static-scan")?.passed === true;
	return validArtifact ? "PARTIAL" : "INVALID";
}

export async function runRealCodePreflight(
	task: RealCodeTask,
	options: Pick<RealCodeRunOptions, "dockerHost" | "commandTimeoutMs"> = {},
): Promise<RealCodePreflightResult> {
	const sandbox = new DockerRealCodeSandbox(task, options);
	await sandbox.start();
	try {
		return await sandbox.preflight();
	} finally {
		await sandbox.close();
	}
}

export async function runRealCodeTask(
	task: RealCodeTask,
	runtime: OnlineRuntime,
	options: RealCodeRunOptions = {},
): Promise<RealCodeEvalResult> {
	const trialIndex = options.trialIndex ?? 1;
	const runId = options.runId ?? `${task.id}-t${trialIndex}-${randomUUID().slice(0, 8)}`;
	const limits = realCodeRunLimits(task, options);
	const startedAt = new Date().toISOString();
	const plans = new PlanStore();
	const trace = new TraceCollector();
	const sandbox = new DockerRealCodeSandbox(task, { ...options, runId });
	const policy = new RealCodeToolPolicy(plans, trace, task.mode === "discovery");
	await sandbox.start();
	let turns = 0;
	let runError: string | undefined;
	let turnLimitReached = false;
	let wallTimeLimitReached = false;
	let lastAssistantStopReason: string | undefined;
	const agent = new Agent({
		initialState: {
			systemPrompt: realCodeSystemPrompt(task),
			model: runtime.model,
			tools: [...createPlanTools(plans), ...createRealCodeTools(sandbox, task.generatedTestRoot)],
		},
		streamFn: runtime.streamFn,
		getApiKey: runtime.getApiKey,
		beforeToolCall: policy.beforeToolCall.bind(policy),
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
			if (turns >= limits.maxTurns && hasToolCalls) {
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
	const wallTimer = limits.maxWallTimeMs
		? setTimeout(() => {
				wallTimeLimitReached = true;
				agent.abort();
			}, limits.maxWallTimeMs)
		: undefined;
	try {
		await agent.prompt(task.prompt);
	} catch (error) {
		runError = error instanceof Error ? error.message : String(error);
		trace.record("run.error", { message: runError });
	} finally {
		if (wallTimer) clearTimeout(wallTimer);
		unsubscribe();
	}
	try {
		const grade = await sandbox.grade();
		const events = trace.getEvents();
		const finalResponse = finalAssistantText(events);
		const terminationReason: RealCodeTerminationReason = classifyTermination({
			wallTimeLimitReached,
			...(runError ? { runError } : {}),
			turnLimitReached,
			...(lastAssistantStopReason ? { lastAssistantStopReason } : {}),
		});
		const turnsUsed = events.filter((event) => event.type === "agent.turn_end").length;
		const outcomeStatus =
			task.mode === "agent-task" ? agentTaskOutcome(grade, finalResponse, terminationReason) : undefined;
		return {
			runId,
			taskId: task.id,
			evaluationMode: task.mode ?? "regression",
			...(task.sourceTaskId ? { sourceTaskId: task.sourceTaskId } : {}),
			trialIndex,
			runtime:
				task.mode === "agent-task"
					? "online-agent-task"
					: task.mode === "discovery"
						? "online-real-code-discovery"
						: "online-real-code",
			model: runtime.model.id,
			startedAt,
			finishedAt: new Date().toISOString(),
			passed: outcomeStatus ? outcomeStatus === "COMPLETED" : grade.passed && runError === undefined,
			...(outcomeStatus ? { outcomeStatus } : {}),
			terminationReason,
			budget: { ...limits, turnsUsed },
			finalResponse,
			...(plans.get() ? { finalPlan: plans.get() } : {}),
			trace: events,
			grade,
			...(runError ? { runError } : {}),
		};
	} finally {
		await sandbox.close();
	}
}
