import { describe, expect, it } from "vitest";
import { getCollaborationHeldoutTasks } from "../src/tasks/collaboration-heldout.ts";
import { getCollaborationHeldoutTasks as getCollaborationHeldoutV1Tasks } from "../src/tasks/collaboration-heldout-v1.ts";

describe("collaboration heldout dataset versioning", () => {
	it("preserves v1 and changes only the three audited measurement cases", () => {
		const v1 = getCollaborationHeldoutV1Tasks();
		const v11 = getCollaborationHeldoutTasks();

		expect(v11.map((task) => task.id)).toEqual(v1.map((task) => task.id));
		for (const task of v1) {
			if (["heldout-08", "heldout-09", "heldout-12"].includes(task.id)) continue;
			expect(v11.find((candidate) => candidate.id === task.id)).toEqual(task);
		}

		const v1Retry = v1.find((task) => task.id === "heldout-08");
		const v11Retry = v11.find((task) => task.id === "heldout-08");
		expect(v1Retry?.prompt).toContain("请使用 heldout-before-commit");
		expect(v11Retry?.prompt).toContain("将 request_id 固定为 heldout-before-commit");

		for (const taskId of ["heldout-09", "heldout-12"]) {
			const oldTask = v1.find((task) => task.id === taskId);
			const revisedTask = v11.find((task) => task.id === taskId);
			expect(oldTask?.expect.notifications).toHaveLength(1);
			expect(revisedTask?.expect.notifications).toBeUndefined();
			expect(revisedTask?.expect.notificationRecipients).toEqual(["qa-lead@example.com"]);
			expect(revisedTask?.requiresModelReview).toBe(true);
		}
	});
});
