import { Agent } from "@earendil-works/pi-agent-core";
import type { Model, StreamFunction } from "@earendil-works/pi-ai";
import { PlanStore } from "../plan/store.ts";
import { createPlanTools } from "../plan/tools.ts";
import type { PolicyProfile } from "../policy/policy.ts";
import { ToolPolicy } from "../policy/policy.ts";
import { TraceCollector } from "../trace/collector.ts";
import type { EvalResult, EvalTask } from "../types.ts";
import { createCollaborationTools } from "../world/collaboration-tools.ts";
import { WorldSandbox } from "../world/sandbox.ts";
import { createWorldTools } from "../world/tools.ts";
import { authorizedWriteTools } from "./authorization.ts";
import { evaluateTask } from "./evaluator.ts";
import { qaSystemPrompt } from "./system-prompt.ts";

function systemPrompt(task: EvalTask): string {
	if (task.category === "qa") {
		return qaSystemPrompt(task);
	}
	return `You are being evaluated as a plan-first agent in a deterministic personal-assistant sandbox.
The current sandbox time is ${task.initialWorld.now}. Resolve relative dates from this value.
First create a concise plan with plan_create. Use read tools before guessing. Use write tools only when needed.
Update the plan as work progresses. If a tool fails, inspect the error and retry only when safe.
The isolated tool state is the source of truth: never claim success without a successful tool result.`;
}

export interface OnlineRuntime {
	model: Model<string>;
	streamFn: StreamFunction;
	getApiKey: () => string;
}

export async function runOnlineTask(
	task: EvalTask,
	runtime: OnlineRuntime,
	trialIndex = 1,
	policyProfile: PolicyProfile = "strict-active",
): Promise<EvalResult> {
	const plans = new PlanStore();
	const trace = new TraceCollector();
	const sandbox = new WorldSandbox(task.initialWorld, task.faults, (invocation, fault) => {
		trace.record("fault.injected", { invocation, fault });
	});
	const policy = new ToolPolicy(plans, trace, sandbox, policyProfile, authorizedWriteTools(task));
	let turns = 0;
	const agent = new Agent({
		initialState: {
			systemPrompt: systemPrompt(task),
			model: runtime.model,
			tools: [...createPlanTools(plans), ...createWorldTools(sandbox), ...createCollaborationTools(sandbox)],
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
		shouldStopAfterTurn: () => ++turns >= (task.category === "qa" ? 20 : 12),
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
	return evaluateTask(task, sandbox.getState(), trace.getEvents(), plans.get() !== undefined, plans.get(), {
		trialIndex,
		runtime: "online",
		model: runtime.model.id,
		policyProfile,
	});
}
