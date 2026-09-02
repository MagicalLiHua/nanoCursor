import { Agent } from "@earendil-works/pi-agent-core";
import {
	fauxAssistantMessage,
	fauxProvider,
	fauxText,
	fauxToolCall,
	type Model,
	type StreamFunction,
} from "@earendil-works/pi-ai";
import { toJsonValue } from "../json.ts";
import { PlanStore } from "../plan/store.ts";
import { createPlanTools } from "../plan/tools.ts";
import type { PolicyProfile } from "../policy/policy.ts";
import { ToolPolicy } from "../policy/policy.ts";
import { HttpSandboxClient, type HttpSandboxEvent } from "../sandbox/http-client.ts";
import { TraceCollector } from "../trace/collector.ts";
import type {
	EvalResult,
	EvalTask,
	HardEvaluationResult,
	JsonValue,
	ModelReviewResult,
	PlanSnapshot,
	TraceEvent,
} from "../types.ts";
import { createCollaborationHttpTools } from "../world/collaboration-http-tools.ts";
import { WorldSandbox } from "../world/sandbox.ts";
import { authorizedWriteTools } from "./authorization.ts";
import { authorizedToolCalls, countPolicyBlocks, evaluateTask, finalAssistantText, toolCalls } from "./evaluator.ts";
import { qaSystemPrompt } from "./system-prompt.ts";

function createScriptedResponses(task: EvalTask) {
	return task.script.map((turn, turnIndex) => {
		const blocks = [
			...(turn.text ? [fauxText(turn.text)] : []),
			...(turn.calls ?? []).map((item, index) =>
				fauxToolCall(item.tool, item.args, {
					id: `${task.id}-http-turn-${turnIndex + 1}-call-${index + 1}-${item.tool}`,
				}),
			),
		];
		return fauxAssistantMessage(blocks, { stopReason: turn.calls?.length ? "toolUse" : "stop" });
	});
}

export interface HttpOnlineRuntime {
	model: Model<string>;
	streamFn: StreamFunction;
	getApiKey: () => string;
}

interface HttpRunOptions {
	baseUrl: string;
	trialIndex?: number;
	policyProfile?: PolicyProfile;
	runtime?: HttpOnlineRuntime;
	runModelReview?: boolean;
}

export function modelReviewEvidence(
	sandboxEvents: HttpSandboxEvent[],
	runtimeEvents: TraceEvent[],
	finalPlan: PlanSnapshot | undefined,
	runtimeToolNames: string[],
): Array<{ [key: string]: JsonValue }> {
	const policyEvidence = runtimeEvents
		.filter((event) => event.type === "policy.decision")
		.map((event) => ({
			id: `runtime-policy-${event.sequence}`,
			type: "runtime_policy_decision",
			data: toJsonValue(event),
		}));
	return [
		{
			id: "runtime-capabilities",
			type: "runtime_capabilities",
			data: toJsonValue({ toolNames: runtimeToolNames }),
		},
		{
			id: "final-plan",
			type: "final_plan",
			data: toJsonValue(finalPlan ?? null),
		},
		...policyEvidence,
		...sandboxEvents.map((event) => ({
			id: `http-event-${event.sequence}`,
			type: "sandbox_event",
			data: toJsonValue(event),
		})),
	];
}

function isOpenTextFailure(message: string): boolean {
	return message.startsWith("Final response is missing:") || message.startsWith("Final response must include one of:");
}

export function combineHardEvaluation(
	hardEvaluation: HardEvaluationResult,
	runtimeFailures: string[],
): HardEvaluationResult {
	const combined = structuredClone(hardEvaluation);
	if (runtimeFailures.length === 0) return combined;
	combined.passed = false;
	combined.checks.push({
		check_id: "pi-runtime-constraints",
		passed: false,
		message: runtimeFailures.join("; "),
		evidence_ids: [],
	});
	return combined;
}

export async function runHttpTask(task: EvalTask, options: HttpRunOptions): Promise<EvalResult> {
	if (task.category !== "qa") throw new Error("HTTP sandbox currently supports only the test-collaboration suite.");
	const trialIndex = options.trialIndex ?? 1;
	const policyProfile = options.policyProfile ?? "strict-active";
	const plans = new PlanStore();
	const trace = new TraceCollector();
	const policyMirror = new WorldSandbox(task.initialWorld);
	const policy = new ToolPolicy(plans, trace, policyMirror, policyProfile, authorizedWriteTools(task));
	const client = new HttpSandboxClient(options.baseUrl);
	await client.initialize(task);
	const runtimeTools = [...createPlanTools(plans), ...createCollaborationHttpTools(client)];
	let model: Model<string>;
	let streamFn: StreamFunction;
	let getApiKey: (() => string) | undefined;
	if (options.runtime) {
		model = options.runtime.model;
		streamFn = options.runtime.streamFn;
		getApiKey = options.runtime.getApiKey;
	} else {
		const faux = fauxProvider({ provider: `faux-http-${task.id}` });
		faux.setResponses(createScriptedResponses(task));
		model = faux.getModel();
		streamFn = faux.provider.streamSimple.bind(faux.provider);
	}
	let turns = 0;
	const agent = new Agent({
		initialState: {
			systemPrompt: qaSystemPrompt(task),
			model,
			tools: runtimeTools,
		},
		streamFn,
		...(getApiKey ? { getApiKey } : {}),
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
		shouldStopAfterTurn: () => ++turns >= 20,
		toolExecution: "sequential",
	});
	const unsubscribe = agent.subscribe((event) => trace.recordAgentEvent(event));
	try {
		await agent.prompt(task.prompt);
		for (const followUp of task.followUpPrompts ?? []) await agent.prompt(followUp);
	} catch (error) {
		trace.record("run.error", { message: error instanceof Error ? error.message : String(error) });
	} finally {
		unsubscribe();
	}
	const world = await client.getState();
	const sandboxEvents = await client.getEvents();
	for (const event of sandboxEvents) trace.record("sandbox.http", event);
	let modelReview: ModelReviewResult | undefined;
	if (task.requiresModelReview && options.runModelReview) {
		try {
			const runtimeEvents = trace.getEvents();
			modelReview = await client.reviewModel({
				taskId: task.id,
				userRequest: [task.prompt, ...(task.followUpPrompts ?? [])].join("\n\n"),
				finalResponse: finalAssistantText(runtimeEvents),
				evidence: modelReviewEvidence(
					sandboxEvents,
					runtimeEvents,
					plans.get(),
					runtimeTools.map((tool) => tool.name),
				),
			});
			trace.record("judge.completed", { scores: modelReview.scores, confidence: modelReview.confidence });
		} catch (error) {
			trace.record("judge.error", { message: error instanceof Error ? error.message : String(error) });
		}
	}
	const events = trace.getEvents();
	const result = evaluateTask(task, world, events, plans.get() !== undefined, plans.get(), {
		trialIndex,
		runtime: options.runtime ? "online-http" : "offline-scripted-http",
		model: model.id,
		policyProfile,
	});
	if (task.requiresModelReview) {
		result.failures = result.failures.filter((failure) => !isOpenTextFailure(failure));
		result.passed = result.failures.length === 0;
		result.metrics.taskSuccess = result.passed;
	}
	const sandboxHardEvaluation = await client.evaluateHardRequirements({
		expectation: task.expect,
		toolCalls: toolCalls(events),
		authorizedToolCalls: authorizedToolCalls(events),
		policyBlocks: countPolicyBlocks(events),
	});
	const hardEvaluation = combineHardEvaluation(sandboxHardEvaluation, result.failures);
	result.hardEvaluation = hardEvaluation;
	if (!hardEvaluation.passed) {
		for (const check of hardEvaluation.checks) {
			if (!check.passed && check.check_id !== "pi-runtime-constraints") {
				result.failures.push(`Sandbox hard check failed (${check.check_id}): ${check.message}`);
			}
		}
		result.passed = false;
		result.metrics.taskSuccess = false;
	}
	result.layeredDecision = await client.decideLayered({
		hardEvaluation,
		...(modelReview ? { modelReview } : {}),
		requiresModelReview: task.requiresModelReview ?? false,
		highRisk: task.highRisk ?? false,
	});
	await client.close();
	return result;
}
