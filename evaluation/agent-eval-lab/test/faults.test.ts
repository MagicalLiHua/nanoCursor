import { describe, expect, it, vi } from "vitest";
import { getTask } from "../src/tasks/catalog.ts";
import type { FaultMode } from "../src/types.ts";
import { WorldSandbox } from "../src/world/sandbox.ts";

describe("fault injection", () => {
	it.each(["error", "timeout", "permission_denied", "rate_limited"] satisfies FaultMode[])(
		"throws a typed failure for %s",
		(mode) => {
			const onFault = vi.fn();
			const sandbox = new WorldSandbox(
				getTask("plan-01").initialWorld,
				[{ toolName: "weather_get", onCall: 1, mode }],
				onFault,
			);
			const invocation = sandbox.nextInvocation("call-1", "weather_get", {});
			expect(() => sandbox.applyFault(invocation)).toThrow();
			expect(onFault).toHaveBeenCalledOnce();
		},
	);

	it.each(["empty_result", "malformed_result"] satisfies FaultMode[])(
		"returns a synthetic-result instruction for %s",
		(mode) => {
			const sandbox = new WorldSandbox(getTask("plan-01").initialWorld, [
				{ toolName: "weather_get", onCall: 1, mode },
			]);
			const invocation = sandbox.nextInvocation("call-1", "weather_get", {});
			expect(sandbox.applyFault(invocation)?.mode).toBe(mode);
		},
	);

	it("raises a write timeout only after the side effect commits", () => {
		const sandbox = new WorldSandbox(getTask("heldout-07").initialWorld, [
			{ toolName: "test_run_create", onCall: 1, mode: "timeout_after_commit" },
		]);
		const invocation = sandbox.nextInvocation("call-after-commit", "test_run_create", {});
		expect(sandbox.applyFault(invocation)).toBeUndefined();
		sandbox.createTestRun("build-40", ["case-idem-a"], "after-commit-unit");
		expect(() => sandbox.throwPostCommitFault("call-after-commit")).toThrow("after commit");
		expect(sandbox.getTestRun({ requestId: "after-commit-unit" })).toBeDefined();
	});
});
