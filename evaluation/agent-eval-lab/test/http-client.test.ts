import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpSandboxClient } from "../src/sandbox/http-client.ts";
import { getTask } from "../src/tasks/catalog.ts";

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("HTTP sandbox client", () => {
	it("forwards the trial id and the trace-derived hard-evaluation input", async () => {
		const fetchMock = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(new Response(JSON.stringify({ trialId: "trial-unit" }), { status: 201 }))
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						passed: true,
						checks: [
							{
								check_id: "required-tool:test_run_get",
								passed: true,
								message: "ok",
								evidence_ids: [],
							},
						],
					}),
					{ status: 200 },
				),
			)
			.mockResolvedValueOnce(new Response(null, { status: 204 }));
		vi.stubGlobal("fetch", fetchMock);
		const task = getTask("qa-01");
		const client = new HttpSandboxClient("http://sandbox.test/", "trial-unit");

		await client.initialize(task);
		const result = await client.evaluateHardRequirements({
			expectation: task.expect,
			toolCalls: ["plan_create", "test_run_get"],
			authorizedToolCalls: ["plan_create", "test_run_get"],
			policyBlocks: 0,
		});
		await client.close();

		expect(result.passed).toBe(true);
		const evaluationCall = fetchMock.mock.calls[1];
		expect(evaluationCall?.[0]).toBe("http://sandbox.test/trials/trial-unit/hard-evaluation");
		const init = evaluationCall?.[1];
		expect(JSON.parse(String(init?.body))).toMatchObject({
			tool_calls: ["plan_create", "test_run_get"],
			authorized_tool_calls: ["plan_create", "test_run_get"],
			policy_blocks: 0,
		});
	});

	it("rejects malformed hard-evaluation responses", async () => {
		const fetchMock = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(new Response(JSON.stringify({ trialId: "trial-bad" }), { status: 201 }))
			.mockResolvedValueOnce(new Response(JSON.stringify({ passed: "yes" }), { status: 200 }));
		vi.stubGlobal("fetch", fetchMock);
		const task = getTask("qa-01");
		const client = new HttpSandboxClient("http://sandbox.test", "trial-bad");
		await client.initialize(task);

		await expect(
			client.evaluateHardRequirements({
				expectation: task.expect,
				toolCalls: [],
				authorizedToolCalls: [],
				policyBlocks: 0,
			}),
		).rejects.toThrow("invalid shape");
	});
});
