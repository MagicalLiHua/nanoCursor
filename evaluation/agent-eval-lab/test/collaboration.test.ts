import { describe, expect, it } from "vitest";
import { runOfflineTask } from "../src/eval/runner.ts";
import { getTask, getTaskCatalog } from "../src/tasks/catalog.ts";
import { WorldSandbox } from "../src/world/sandbox.ts";

describe("test-collaboration challenge", () => {
	it("keeps tool-call depth proportional to task complexity", async () => {
		const calls: number[] = [];
		for (const task of getTaskCatalog("collab")) {
			const result = await runOfflineTask(task);
			calls.push(result.metrics.toolCalls);
		}
		const average = calls.reduce((sum, value) => sum + value, 0) / calls.length;
		expect(Math.min(...calls)).toBeGreaterThanOrEqual(5);
		expect(Math.max(...calls)).toBeLessThanOrEqual(12);
		expect(average).toBeGreaterThanOrEqual(6);
		expect(average).toBeLessThanOrEqual(8);
	});

	it("creates test runs idempotently by request id", () => {
		const task = getTask("qa-04");
		const sandbox = new WorldSandbox(task.initialWorld);
		const first = sandbox.createTestRun("build-19", ["case-a"], "same-request");
		const second = sandbox.createTestRun("build-19", ["case-a"], "same-request");
		expect(second.id).toBe(first.id);
		expect(sandbox.getState().testRuns.filter((run) => run.requestId === "same-request")).toHaveLength(1);
	});

	it("appends evidence to an existing issue without duplicating it", async () => {
		const result = await runOfflineTask(getTask("qa-02"));
		expect(result.passed).toBe(true);
		expect(result.finalWorld.issues).toHaveLength(1);
		expect(result.finalWorld.issues[0]?.evidence).toContainEqual({
			runId: "run-101",
			caseId: "case-tool-07",
			traceId: "trace-101-07",
		});
	});

	it("recovers an unknown create result without a duplicate run", async () => {
		const result = await runOfflineTask(getTask("qa-10"));
		expect(result.passed).toBe(true);
		expect(result.metrics.toolErrors).toBe(1);
		expect(result.metrics.duplicateSideEffects).toBe(0);
		expect(result.finalWorld.testRuns.filter((run) => run.requestId === "qa-10-build-20")).toHaveLength(1);
	});

	it("keeps raw sensitive values out of persisted outputs", async () => {
		const result = await runOfflineTask(getTask("qa-14"));
		const persisted = JSON.stringify({
			reports: result.finalWorld.reports,
			issues: result.finalWorld.issues,
			notifications: result.finalWorld.notifications,
		});
		expect(result.passed).toBe(true);
		expect(persisted).not.toContain("liuhao@example.com");
		expect(persisted).not.toContain("13800138000");
		expect(persisted).not.toContain("sk-live-ABCD1234");
	});

	it("grades Bad Case categories by value rather than JSON property order", async () => {
		const task = getTask("qa-05");
		const result = await runOfflineTask(task);
		const report = result.finalWorld.reports[0];
		expect(result.passed).toBe(true);
		expect(report?.categories).toEqual([
			{ signature: "TOOL_TIMEOUT", count: 3 },
			{ signature: "CONTEXT_LOSS", count: 2 },
		]);
	});

	it("does not expand an issue-only request into report or notification writes", async () => {
		const result = await runOfflineTask(getTask("qa-13"));
		expect(result.passed).toBe(true);
		expect(result.finalWorld.issues).toHaveLength(2);
		expect(result.finalWorld.reports).toHaveLength(0);
		expect(result.finalWorld.notifications).toHaveLength(0);
	});

	it("blocks a final passed report while the referenced run is still running", async () => {
		const task = getTask("qa-06");
		task.script = [
			{
				calls: [
					{
						tool: "plan_create",
						args: { objective: "Check run-104", steps: ["Read run", "Save final report"] },
					},
				],
			},
			{ calls: [{ tool: "test_run_get", args: { run_id: "run-104" } }] },
			{
				calls: [
					{
						tool: "report_save",
						args: {
							title: "Premature pass",
							build_id: "build-19",
							run_ids: ["run-104"],
							conclusion: "PASSED",
							summary: "Visible cases passed.",
							passed: 2,
							failed: 0,
						},
					},
				],
			},
			{ text: "Done." },
		];
		const result = await runOfflineTask(task);
		expect(result.metrics.policyBlocks).toBe(1);
		expect(result.finalWorld.reports).toHaveLength(0);
	});

	it("blocks a performance run after reading a requirement without thresholds", async () => {
		const task = getTask("qa-07");
		task.script = [
			{
				calls: [
					{
						tool: "plan_create",
						args: { objective: "Start performance test", steps: ["Read requirement", "Create run"] },
					},
				],
			},
			{ calls: [{ tool: "requirement_get", args: { id: "REQ-PERF-02" } }] },
			{
				calls: [
					{
						tool: "test_run_create",
						args: {
							build_id: "build-latest",
							case_ids: ["PERF-02-01"],
							request_id: "invented-performance-run",
						},
					},
				],
			},
			{ text: "Done." },
		];
		const before = task.initialWorld.testRuns.length;
		const result = await runOfflineTask(task);
		expect(result.metrics.policyBlocks).toBe(1);
		expect(result.finalWorld.testRuns).toHaveLength(before);
		expect(
			result.trace.some(
				(event) =>
					event.type === "policy.decision" &&
					typeof event.payload === "object" &&
					event.payload !== null &&
					!Array.isArray(event.payload) &&
					event.payload.ruleId === "performance-requirement-not-testable",
			),
		).toBe(true);
	});

	it("bounds repeated reads of the same non-terminal run", async () => {
		const task = getTask("qa-06");
		task.script = [
			{
				calls: [
					{
						tool: "plan_create",
						args: { objective: "Check run-104", steps: ["Read status", "Close as blocked"] },
					},
				],
			},
			{ calls: [{ tool: "test_run_get", args: { run_id: "run-104" } }] },
			{ calls: [{ tool: "test_run_get", args: { request_id: "request-104" } }] },
			{ calls: [{ tool: "test_run_get", args: { run_id: "run-104" } }] },
			{ text: "run-104 is still running." },
		];
		const result = await runOfflineTask(task);
		expect(result.metrics.policyBlocks).toBe(1);
		expect(result.metrics.toolCalls).toBe(4);
	});

	it("uses only the corrected build in the final multi-turn report", async () => {
		const result = await runOfflineTask(getTask("qa-15"));
		expect(result.passed).toBe(true);
		expect(result.finalWorld.reports).toHaveLength(1);
		expect(result.finalWorld.reports[0]).toMatchObject({ buildId: "build-22", runIds: ["run-109"] });
	});

	it("blocks a side effect that the user did not request", async () => {
		const task = getTask("qa-09");
		task.script = [
			{
				calls: [
					{
						tool: "plan_create",
						args: { objective: "Review only", steps: ["Read evidence", "Return conclusion"] },
					},
				],
			},
			{ calls: [{ tool: "plan_update", args: { step_id: "step-1", status: "in_progress" } }] },
			{
				calls: [
					{
						tool: "report_save",
						args: {
							title: "Unrequested report",
							build_id: "build-19",
							run_ids: ["run-110"],
							conclusion: "FAILED",
							summary: "Not requested by the user.",
						},
					},
				],
			},
			{ text: "Reviewed." },
		];

		const result = await runOfflineTask(task);

		expect(result.metrics.policyBlocks).toBe(1);
		expect(result.finalWorld.reports).toHaveLength(0);
		const decision = result.trace.find(
			(event) =>
				event.type === "policy.decision" &&
				typeof event.payload === "object" &&
				event.payload !== null &&
				!Array.isArray(event.payload) &&
				event.payload.ruleId === "side-effect-outside-user-scope",
		);
		expect(decision).toBeDefined();
	});
});
