import { describe, expect, it } from "vitest";
import { getTaskCatalog, validateTaskCatalog } from "../src/tasks/catalog.ts";

describe("task catalog", () => {
	it("contains the development and frozen collaboration suites", () => {
		const tasks = getTaskCatalog("all");
		expect(getTaskCatalog("baseline")).toHaveLength(30);
		expect(getTaskCatalog("hard")).toHaveLength(10);
		expect(getTaskCatalog("hard-v2")).toHaveLength(10);
		expect(getTaskCatalog("collab")).toHaveLength(15);
		expect(getTaskCatalog("collab-heldout")).toHaveLength(12);
		expect(getTaskCatalog("collab-heldout-v1")).toHaveLength(12);
		expect(getTaskCatalog("collab-heldout-v1.1")).toHaveLength(12);
		expect(getTaskCatalog("collab-regression-v1.1")).toHaveLength(12);
		expect(getTaskCatalog("collab-final-v2")).toHaveLength(15);
		expect(getTaskCatalog("collab-heldout")).toEqual(getTaskCatalog("collab-heldout-v1.1"));
		expect(getTaskCatalog("collab-regression-v1.1")).toEqual(getTaskCatalog("collab-heldout-v1.1"));
		expect(tasks).toHaveLength(92);
		expect(new Set(tasks.map((task) => task.id)).size).toBe(92);
		expect(validateTaskCatalog(tasks)).toEqual([]);
	});

	it("keeps final-test authorization separate and covers the declared difficulty mix", () => {
		const tasks = getTaskCatalog("collab-final-v2");
		expect(tasks.every((task) => task.authorization !== undefined)).toBe(true);
		expect(tasks.every((task) => task.benchmark?.split === "final-test")).toBe(true);
		expect(tasks.filter((task) => task.benchmark?.difficulty === "basic")).toHaveLength(3);
		expect(tasks.filter((task) => task.benchmark?.difficulty === "composite")).toHaveLength(8);
		expect(tasks.filter((task) => task.benchmark?.difficulty === "hard")).toHaveLength(4);
	});
});
