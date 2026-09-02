import { describe, expect, it } from "vitest";
import { PlanStore } from "../src/plan/store.ts";

describe("PlanStore", () => {
	it("creates and revises a plan", () => {
		const store = new PlanStore(() => new Date("2026-09-01T00:00:00.000Z"));
		const created = store.create("完成任务", ["读取", "执行"]);
		expect(created.revision).toBe(1);
		expect(created.steps[0]?.status).toBe("in_progress");
		const updated = store.update("step-1", "completed", "完成");
		expect(updated.revision).toBe(2);
		expect(updated.steps[0]).toMatchObject({ status: "completed", note: "完成" });
	});
});
