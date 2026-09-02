import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Type } from "@earendil-works/pi-ai";
import type { PlanSnapshot } from "../types.ts";
import type { PlanStore } from "./store.ts";

const PlanCreateParameters = Type.Object({
	objective: Type.String({ minLength: 1 }),
	steps: Type.Array(Type.String({ minLength: 1 }), { minItems: 1 }),
});

const PlanUpdateParameters = Type.Object({
	step_id: Type.String({ minLength: 1 }),
	status: Type.Union([
		Type.Literal("pending"),
		Type.Literal("in_progress"),
		Type.Literal("completed"),
		Type.Literal("blocked"),
	]),
	note: Type.Optional(Type.String()),
});

function result(snapshot: PlanSnapshot) {
	return {
		content: [{ type: "text" as const, text: JSON.stringify(snapshot) }],
		details: snapshot,
	};
}

export function createPlanTools(store: PlanStore): AgentTool[] {
	const createTool: AgentTool<typeof PlanCreateParameters, PlanSnapshot> = {
		name: "plan_create",
		label: "Create plan",
		description: "Create an explicit multi-step plan before performing a task.",
		parameters: PlanCreateParameters,
		executionMode: "sequential",
		async execute(_toolCallId, params) {
			return result(store.create(params.objective, params.steps));
		},
	};
	const updateTool: AgentTool<typeof PlanUpdateParameters, PlanSnapshot> = {
		name: "plan_update",
		label: "Update plan",
		description:
			"Update one plan step as work progresses. Before the final response, leave no pending or in_progress steps: if a prerequisite is blocked, mark every dependent remaining step blocked too.",
		parameters: PlanUpdateParameters,
		executionMode: "sequential",
		async execute(_toolCallId, params) {
			return result(store.update(params.step_id, params.status, params.note));
		},
	};
	return [createTool, updateTool];
}
