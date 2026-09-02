import { describe, expect, it } from "vitest";
import { runOfflineTask } from "../src/eval/runner.ts";
import { getTask, getTaskCatalog } from "../src/tasks/catalog.ts";

describe("offline runner", () => {
	it("executes a planned side effect end to end", async () => {
		const result = await runOfflineTask(getTask("calendar-01"));
		expect(result.passed).toBe(true);
		expect(result.finalWorld.calendar.some((event) => event.title === "论文讨论会")).toBe(true);
	});

	it("recovers after an injected tool failure", async () => {
		const result = await runOfflineTask(getTask("recovery-01"));
		expect(result.passed).toBe(true);
		expect(result.metrics.toolErrors).toBe(1);
	});

	it("blocks an unplanned side effect", async () => {
		const result = await runOfflineTask(getTask("policy-01"));
		expect(result.passed).toBe(true);
		expect(result.metrics.policyBlocks).toBe(1);
		expect(result.finalWorld.notifications).toEqual([]);
	});

	it("passes the complete deterministic suite", async () => {
		for (const task of getTaskCatalog("all")) {
			const result = await runOfflineTask(task);
			expect(result.passed, `${task.id}: ${result.failures.join("; ")}`).toBe(true);
		}
	});

	it("executes the 15-task test-collaboration oracle", async () => {
		for (const task of getTaskCatalog("collab")) {
			const result = await runOfflineTask(task);
			expect(result.passed, `${task.id}: ${result.failures.join("; ")}`).toBe(true);
		}
	});

	it("executes the 12-task frozen collaboration oracle", async () => {
		for (const task of getTaskCatalog("collab-heldout")) {
			const result = await runOfflineTask(task);
			expect(result.passed, `${task.id}: ${result.failures.join("; ")}`).toBe(true);
		}
	});

	it("keeps the archived v1 collaboration oracle executable", async () => {
		for (const task of getTaskCatalog("collab-heldout-v1")) {
			const result = await runOfflineTask(task);
			expect(result.passed, `${task.id}: ${result.failures.join("; ")}`).toBe(true);
		}
	});
});
