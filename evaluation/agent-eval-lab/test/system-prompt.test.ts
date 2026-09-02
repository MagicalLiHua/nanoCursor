import { describe, expect, it } from "vitest";
import { approvedWriteScope, qaSystemPrompt } from "../src/eval/system-prompt.ts";
import { getCollaborationBenchmarkV2Tasks } from "../src/tasks/collaboration-benchmark-v2.ts";
import { getCollaborationHeldoutTasks } from "../src/tasks/collaboration-heldout.ts";

function heldout(id: string) {
	const task = getCollaborationHeldoutTasks().find((candidate) => candidate.id === id);
	if (!task) throw new Error(`Missing task: ${id}`);
	return task;
}

describe("QA system prompt write-scope contract", () => {
	it("exposes only approved side-effect types without exposing read-tool sequence", () => {
		const task = heldout("heldout-01");

		expect(approvedWriteScope(task)).toEqual(["report_save"]);
		const prompt = qaSystemPrompt(task);
		expect(prompt).toContain("Approved write scope for this task: report_save (Report)");
		expect(prompt).not.toContain("issue_create_or_append (Issue)");
		expect(prompt).not.toContain("test_run_get,");
	});

	it("states that analysis-only tasks have no approved writes", () => {
		const prompt = qaSystemPrompt(heldout("heldout-04"));

		expect(approvedWriteScope(heldout("heldout-04"))).toEqual([]);
		expect(prompt).toContain("Approved write scope for this task: none");
		expect(prompt).toContain("Do not plan or call any side-effect tool");
	});

	it("keeps multiple explicitly requested deliverables", () => {
		expect(approvedWriteScope(heldout("heldout-09"))).toEqual(["notification_send", "report_save"]);
	});

	it("uses independent user authorization instead of grader expectations for final-test tasks", () => {
		const task = getCollaborationBenchmarkV2Tasks().find((candidate) => candidate.id === "final-v2-03");
		if (!task) throw new Error("Missing final-v2-03");

		expect(task.expect.forbiddenTools).toContain("issue_create_or_append");
		expect(approvedWriteScope(task)).toEqual(["issue_create_or_append"]);
		expect(qaSystemPrompt(task)).toContain("issue_create_or_append (Issue)");
	});
});
