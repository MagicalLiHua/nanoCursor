import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { runOfflineSuite } from "../src/eval/runner.ts";
import { getCollaborationBenchmarkV2Tasks } from "../src/tasks/collaboration-benchmark-v2.ts";

const FROZEN_SHA256 = "96175b0937e9ba7a7d402697933acc51ee4191148c7d700e27ad3eda712801f1";

describe("collaboration benchmark v2", () => {
	it("has unique scenarios and capability metadata", () => {
		const tasks = getCollaborationBenchmarkV2Tasks();
		expect(new Set(tasks.map((task) => task.id)).size).toBe(15);
		expect(new Set(tasks.map((task) => task.benchmark?.scenario)).size).toBe(15);
		expect(tasks.every((task) => (task.benchmark?.capabilities.length ?? 0) >= 2)).toBe(true);
	});

	it("passes the deterministic Oracle under strict-active policy", async () => {
		const results = await runOfflineSuite(getCollaborationBenchmarkV2Tasks(), 1, "strict-active");
		expect(results.filter((result) => !result.passed).map((result) => [result.taskId, result.failures])).toEqual([]);
	});

	it("matches the frozen dataset fingerprint", () => {
		const fingerprint = createHash("sha256").update(JSON.stringify(getCollaborationBenchmarkV2Tasks())).digest("hex");
		expect(fingerprint).toBe(FROZEN_SHA256);
	});
});
