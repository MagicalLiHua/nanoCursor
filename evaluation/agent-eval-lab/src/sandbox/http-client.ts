import { randomUUID } from "node:crypto";
import type {
	EvalTask,
	HardEvaluationResult,
	JsonValue,
	LayeredDecision,
	ModelReviewResult,
	TaskExpectation,
	WorldState,
} from "../types.ts";

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorMessage(status: number, body: string): string {
	try {
		const parsed: unknown = JSON.parse(body);
		if (isRecord(parsed) && typeof parsed.detail === "string") return `HTTP ${status}: ${parsed.detail}`;
	} catch {
		// The invalid body is itself useful evidence for malformed-result tasks.
	}
	return `HTTP ${status}: ${body || "empty response"}`;
}

export interface HttpSandboxEvent {
	sequence: number;
	toolName: string;
	callIndex: number;
	status: string;
	faultMode: string | null;
	request: JsonValue;
	response: JsonValue;
	createdAt: string;
}

export class HttpSandboxClient {
	readonly baseUrl: string;
	readonly trialId: string;
	private initialized = false;

	constructor(baseUrl: string, trialId?: string) {
		this.baseUrl = baseUrl.replace(/\/$/, "");
		this.trialId = trialId ?? `trial-${randomUUID()}`;
	}

	async initialize(task: EvalTask): Promise<void> {
		await this.request(
			"/trials",
			{
				method: "POST",
				body: JSON.stringify({
					trial_id: this.trialId,
					initial_world: task.initialWorld,
					faults: task.faults ?? [],
				}),
			},
			false,
		);
		this.initialized = true;
	}

	async request(path: string, init: RequestInit = {}, includeTrial = true): Promise<JsonValue> {
		const headers = new Headers(init.headers);
		headers.set("accept", "application/json");
		if (init.body !== undefined) headers.set("content-type", "application/json");
		if (includeTrial) {
			if (!this.initialized) throw new Error("HTTP sandbox trial has not been initialized.");
			headers.set("x-trial-id", this.trialId);
		}
		const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
		const body = await response.text();
		if (!response.ok) throw new Error(errorMessage(response.status, body));
		if (body.length === 0) return null;
		try {
			return JSON.parse(body) as JsonValue;
		} catch {
			throw new Error(`Malformed JSON from sandbox ${path}: ${body}`);
		}
	}

	async getState(): Promise<WorldState> {
		const value = await this.request(`/trials/${encodeURIComponent(this.trialId)}/state`, {}, false);
		if (!isRecord(value)) throw new Error("Sandbox state response must be an object.");
		return value as unknown as WorldState;
	}

	async getEvents(): Promise<HttpSandboxEvent[]> {
		const value = await this.request(`/trials/${encodeURIComponent(this.trialId)}/events`, {}, false);
		if (!Array.isArray(value)) throw new Error("Sandbox events response must be an array.");
		return value as unknown as HttpSandboxEvent[];
	}

	async evaluateHardRequirements(input: {
		expectation: TaskExpectation;
		toolCalls: string[];
		authorizedToolCalls: string[];
		policyBlocks: number;
	}): Promise<HardEvaluationResult> {
		const value = await this.request(
			`/trials/${encodeURIComponent(this.trialId)}/hard-evaluation`,
			{
				method: "POST",
				body: JSON.stringify({
					expectation: input.expectation,
					tool_calls: input.toolCalls,
					authorized_tool_calls: input.authorizedToolCalls,
					policy_blocks: input.policyBlocks,
				}),
			},
			false,
		);
		if (!isRecord(value) || typeof value.passed !== "boolean" || !Array.isArray(value.checks)) {
			throw new Error("Sandbox hard-evaluation response has an invalid shape.");
		}
		return value as unknown as HardEvaluationResult;
	}

	async decideLayered(input: {
		hardEvaluation: HardEvaluationResult;
		modelReview?: ModelReviewResult;
		requiresModelReview: boolean;
		judgeConflict?: boolean;
		highRisk?: boolean;
	}): Promise<LayeredDecision> {
		const value = await this.request(
			"/decisions",
			{
				method: "POST",
				body: JSON.stringify({
					hard_evaluation: input.hardEvaluation,
					...(input.modelReview ? { model_review: input.modelReview } : {}),
					requires_model_review: input.requiresModelReview,
					judge_conflict: input.judgeConflict ?? false,
					high_risk: input.highRisk ?? false,
				}),
			},
			false,
		);
		if (
			!isRecord(value) ||
			(value.status !== "PASS" && value.status !== "FAIL" && value.status !== "REVIEW") ||
			!Array.isArray(value.hard_checks) ||
			!Array.isArray(value.review_reasons)
		) {
			throw new Error("Sandbox layered-decision response has an invalid shape.");
		}
		return value as unknown as LayeredDecision;
	}

	async reviewModel(input: {
		taskId: string;
		userRequest: string;
		finalResponse: string;
		evidence: Array<{ [key: string]: JsonValue }>;
	}): Promise<ModelReviewResult> {
		const value = await this.request(
			"/model-reviews",
			{
				method: "POST",
				body: JSON.stringify({
					task_id: input.taskId,
					user_request: input.userRequest,
					final_response: input.finalResponse,
					evidence: input.evidence,
				}),
			},
			false,
		);
		if (
			!isRecord(value) ||
			!isRecord(value.scores) ||
			!Array.isArray(value.evidence_ids) ||
			!Array.isArray(value.deductions) ||
			typeof value.confidence !== "number"
		) {
			throw new Error("Sandbox model-review response has an invalid shape.");
		}
		return value as unknown as ModelReviewResult;
	}

	async close(): Promise<void> {
		if (!this.initialized) return;
		await this.request(`/trials/${encodeURIComponent(this.trialId)}`, { method: "DELETE" }, false);
		this.initialized = false;
	}
}
