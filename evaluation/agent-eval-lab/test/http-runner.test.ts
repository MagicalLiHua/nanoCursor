import { describe, expect, it } from "vitest";
import { combineHardEvaluation, modelReviewEvidence } from "../src/eval/http-runner.ts";

describe("HTTP runner layered hard checks", () => {
	it("cannot report PASS when Pi runtime constraints failed", () => {
		const combined = combineHardEvaluation(
			{
				passed: true,
				checks: [{ check_id: "state", passed: true, message: "state is correct", evidence_ids: [] }],
			},
			["The agent finished with incomplete plan steps."],
		);

		expect(combined.passed).toBe(false);
		expect(combined.checks).toContainEqual({
			check_id: "pi-runtime-constraints",
			passed: false,
			message: "The agent finished with incomplete plan steps.",
			evidence_ids: [],
		});
	});

	it("preserves a passing sandbox result when runtime constraints pass", () => {
		const hard = {
			passed: true,
			checks: [{ check_id: "state", passed: true, message: "state is correct", evidence_ids: [] }],
		};

		expect(combineHardEvaluation(hard, [])).toEqual(hard);
	});

	it("gives the model reviewer plan, capability, policy, and sandbox evidence", () => {
		const evidence = modelReviewEvidence(
			[
				{
					sequence: 7,
					toolName: "requirement_get",
					callIndex: 1,
					status: "success",
					faultMode: null,
					request: { id: "REQ-PERF-02" },
					response: { thresholds: [] },
					createdAt: "2026-08-30T00:00:00.000Z",
				},
			],
			[
				{
					sequence: 3,
					timestamp: "2026-08-30T00:00:00.000Z",
					type: "policy.decision",
					payload: { allowed: false, ruleId: "side-effect-outside-user-scope" },
				},
			],
			{
				objective: "Run the performance test",
				revision: 2,
				steps: [{ id: "step-1", title: "Confirm threshold", status: "blocked" }],
				updatedAt: "2026-08-30T00:00:00.000Z",
			},
			["plan_create", "requirement_get"],
		);

		expect(evidence.map((item) => item.id)).toEqual([
			"runtime-capabilities",
			"final-plan",
			"runtime-policy-3",
			"http-event-7",
		]);
	});
});
