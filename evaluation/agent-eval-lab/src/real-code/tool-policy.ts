import type { BeforeToolCallContext, BeforeToolCallResult } from "@earendil-works/pi-agent-core";
import type { PlanStore } from "../plan/store.ts";
import type { TraceCollector } from "../trace/collector.ts";
import type { PolicyDecision } from "../types.ts";

export class RealCodeToolPolicy {
	private readonly plans: PlanStore;
	private readonly trace: TraceCollector;
	private readonly discovery: boolean;
	private discoveryReadCalls = 0;
	private hasWrittenDiscoveryTest = false;

	constructor(plans: PlanStore, trace: TraceCollector, discovery = false) {
		this.plans = plans;
		this.trace = trace;
		this.discovery = discovery;
	}

	async beforeToolCall(
		context: BeforeToolCallContext,
		signal?: AbortSignal,
	): Promise<BeforeToolCallResult | undefined> {
		signal?.throwIfAborted();
		const decision = this.decide(context.toolCall.name);
		this.trace.record("policy.decision", {
			toolCallId: context.toolCall.id,
			toolName: context.toolCall.name,
			...decision,
		});
		return decision.allowed ? undefined : { block: true, reason: decision.reason };
	}

	private decide(toolName: string): PolicyDecision {
		const isRepositoryRead = ["repo_list", "repo_search", "repo_read"].includes(toolName);
		if (this.discovery && isRepositoryRead && !this.plans.get()) {
			return {
				allowed: false,
				ruleId: "discovery-read-requires-plan",
				reason: "Create a concise risk-based plan before exploring the repository.",
			};
		}
		if (this.discovery && isRepositoryRead && !this.hasWrittenDiscoveryTest) {
			if (this.discoveryReadCalls >= 12) {
				return {
					allowed: false,
					ruleId: "discovery-initial-exploration-budget",
					reason:
						"Initial exploration budget exhausted. Write and run the smallest defensible test now; inspect more only after test_write.",
				};
			}
			this.discoveryReadCalls += 1;
		}
		if (toolName !== "test_write") {
			return { allowed: true, ruleId: "read-or-plan", reason: "Read, execution, or plan tool." };
		}
		if (!this.plans.get()) {
			return {
				allowed: false,
				ruleId: "generated-test-write-requires-plan",
				reason: "Create a plan before writing generated regression tests.",
			};
		}
		if (!this.plans.hasActiveStep()) {
			return {
				allowed: false,
				ruleId: "generated-test-write-requires-active-step",
				reason: "Keep one plan step in progress while writing generated regression tests.",
			};
		}
		if (this.discovery) this.hasWrittenDiscoveryTest = true;
		return { allowed: true, ruleId: "planned-generated-test-write", reason: "Active plan permits test output." };
	}
}
