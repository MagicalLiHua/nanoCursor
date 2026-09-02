import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";
import { normalizeOpenAIBaseUrl, normalizeAgentEvalProvider } from "../src/config.ts";
import { PlanStore } from "../src/plan/store.ts";
import { classifyTermination, realCodeRunLimits } from "../src/real-code/agent-protocol.ts";
import { getAgentTask, getAgentTasks } from "../src/real-code/agent-tasks.ts";
import { getRealCodeDiscoveryTask, getRealCodeDiscoveryTasks } from "../src/real-code/discovery-tasks.ts";
import {
	buildIssueAgentTestCommand,
	evaluateIssueGrade,
	evaluateRealCodeGrade,
	findSuspiciousPatterns,
	isDependencyLockPath,
	isIssueTaskTestPath,
	isRepositoryTestPath,
	isRuntimeArtifactPath,
	normalizeGeneratedTestPath,
	normalizeRepositoryPath,
	parseGitApplyNumstat,
	validateIssueCommand,
	validateIssueDeletePath,
} from "../src/real-code/docker-sandbox.ts";
import { buildIssueFrozenManifest } from "../src/real-code/issue-manifest.ts";
import { isProviderInfrastructureError } from "../src/real-code/issue-protocol.ts";
import { issueAgentSystemPrompt } from "../src/real-code/issue-system-prompt.ts";
import { getIssueTask, getIssueTasks } from "../src/real-code/issue-tasks.ts";
import type { IssueChangedFileArtifact } from "../src/real-code/issue-types.ts";
import { realCodeSystemPrompt } from "../src/real-code/system-prompt.ts";
import { getRealCodeTask, getRealCodeTasks } from "../src/real-code/tasks.ts";
import { RealCodeToolPolicy } from "../src/real-code/tool-policy.ts";
import type { ProcessResult } from "../src/real-code/types.ts";
import { TraceCollector } from "../src/trace/collector.ts";

function processResult(exitCode: number, stdout = "", timedOut = false): ProcessResult {
	return { exitCode, stdout, stderr: "", durationMs: 10, timedOut };
}

function changedFile(path: string, status = " M"): IssueChangedFileArtifact {
	return { path, status, bytes: 3, sha256: "abc", content: "new" };
}

describe("real Python code benchmark", () => {
	it("freezes real-code tasks and evaluator assets", async () => {
		const tasks = getRealCodeTasks();
		expect(tasks.map((task) => task.id)).toEqual([
			"real-pytest-10051",
			"real-pytest-7490",
			"real-pylint-7080",
			"real-pylint-6903",
			"real-sphinx-10323",
			"real-sphinx-11445",
			"real-django-16454",
			"real-django-14580",
		]);
		const expectedHashes: Record<string, [string, string]> = {
			"real-pytest-10051": [
				"9662c12536a07d0cc5a6ce6f781edb31ea013af4df0e156522ca624aaed634c0",
				"c25a4ee0d66c1dc4c219f3bd54a0f9b7a9d96513d2d3a42cecb050b7182f3557",
			],
			"real-pytest-7490": [
				"88a7af7e123619306d887c6a9bd1f905acc4872b454df914094c038b71356e30",
				"fe5323cfe9d6be9648be22ffb13a95079c6e17c13f09987f4c1b8589d27c690c",
			],
			"real-pylint-7080": [
				"f908ad1f6d4b8df3755a634c6741abf8ae613bc59278a54c16560c16d255580f",
				"7d42b9582d23667623d84307132d46e63588cbcfe63c35bccb7a52940bf5ca08",
			],
			"real-pylint-6903": [
				"4b0aa53db6637dcaab2ff2373a8ebf3495df12181640b7dd38e5bb19c0e9fbcd",
				"e69b5e44c833af7f8d83cc91d97fe7bf7ac47ac1c24bfa8ab2a3ee47cd60121a",
			],
			"real-sphinx-10323": [
				"c5ca13567dea50fa6203dd33dbb0e89bf85d8e0854ade535fae80a530815f024",
				"6acbd2c641e21aea1adad8c9630746cd41d6d432a9bb2f79137b5c1939566231",
			],
			"real-sphinx-11445": [
				"951e9680cb0d3ba0ab4ebea2e6e4092167e5e7c273420e9aaecd8a8755585cde",
				"b8eeaa101b3c97e7a51666947c4456dafbbed133efb186d5f2550f5e48d53cf4",
			],
			"real-django-16454": [
				"c6d58006c0fc2adb0d3e2c0a3639767ffda1306b493f820480cd9570028d95ce",
				"c89169ebeabcaca5c33f07e01d15f3dcec5e0ead550a616d707e8c613badb192",
			],
			"real-django-14580": [
				"b86a035653ee3b5dabb56972ff8895b08b514e6de5a603e194ceeb9ed227f7c8",
				"e95e4b72c5440d33c93e6c924465a5ea99532f13eefc25396a881407f0f25838",
			],
		};
		for (const task of tasks) {
			const gold = await readFile(task.goldPatchPath);
			const upstreamTests = await readFile(task.upstreamTestPatchPath);
			expect(createHash("sha256").update(gold).digest("hex")).toBe(expectedHashes[task.id]?.[0]);
			expect(createHash("sha256").update(upstreamTests).digest("hex")).toBe(expectedHashes[task.id]?.[1]);
		}
	});

	it("keeps repository reads relative and test writes in the dedicated directory", () => {
		expect(normalizeRepositoryPath("src/_pytest/logging.py")).toBe("src/_pytest/logging.py");
		expect(normalizeGeneratedTestPath("testing/agent_generated/test_caplog.py")).toBe(
			"testing/agent_generated/test_caplog.py",
		);
		expect(normalizeGeneratedTestPath("tests/agent_generated/test_runner.py", "tests/agent_generated")).toBe(
			"tests/agent_generated/test_runner.py",
		);
		expect(() => normalizeRepositoryPath("../gold.patch")).toThrow("Path traversal");
		expect(() => normalizeRepositoryPath(".git/config")).toThrow("Git metadata");
		expect(() => normalizeGeneratedTestPath("src/_pytest/test_patch.py")).toThrow("must be written under");
	});

	it("uses repository-declared test boundaries and ignores runtime build output", () => {
		const django = getIssueTask("issue-eval-django-11141");
		const sphinx = getIssueTask("issue-eval-sphinx-10449");
		expect(isIssueTaskTestPath(django, "tests/migrations/test_loader.py")).toBe(true);
		expect(isIssueTaskTestPath(django, "django/test/client.py")).toBe(false);
		expect(isIssueTaskTestPath(sphinx, "tests/test_ext_autodoc_configs.py")).toBe(true);
		expect(isIssueTaskTestPath(sphinx, "sphinx/testing/util.py")).toBe(false);
		expect(isRuntimeArtifactPath("build/lib/requests/models.py")).toBe(true);
		expect(isRuntimeArtifactPath("requests/models.py")).toBe(false);
		// The grader applies this helper only to untracked entries; tracked build files remain visible changes.
	});

	it("parses evaluator patch paths without treating them as Agent-visible assets", () => {
		expect(
			parseGitApplyNumstat(
				"11\t0\ttests/migrations/test_loader.py\n15\t0\ttests/migrations/test_namespace/0001_initial.py\n",
			),
		).toEqual(["tests/migrations/test_loader.py", "tests/migrations/test_namespace/0001_initial.py"]);
	});

	it("passes only when generated tests fail on buggy, pass on fixed, and preserve regression tests", () => {
		const grade = evaluateRealCodeGrade({
			generatedFiles: ["testing/agent_generated/test_behavior.py"],
			generatedTests: [],
			unexpectedChanges: [],
			suspiciousPatterns: [],
			buggyRun: processResult(1, "FAILED test_behavior.py::test_contract - AssertionError"),
			fixedRun: processResult(0),
			regressionRun: processResult(0),
		});
		expect(grade.passed).toBe(true);
		expect(grade.checks.every((check) => check.passed)).toBe(true);
	});

	it.each([
		["collection error", "ERROR collecting test_bad.py\nImportError: missing dependency", false],
		["syntax error", "ERROR collecting test_bad.py\nSyntaxError: invalid syntax", false],
		["no tests", "collected 0 items\nno tests ran", false],
		["assertion failure", "FAILED test_behavior.py::test_contract - AssertionError", true],
	])("classifies %s on the buggy run", (_name, output, expected) => {
		const grade = evaluateRealCodeGrade({
			generatedFiles: ["testing/agent_generated/test_behavior.py"],
			unexpectedChanges: [],
			suspiciousPatterns: [],
			buggyRun: processResult(1, output),
			fixedRun: processResult(0),
			regressionRun: processResult(0),
		});
		expect(grade.checks.find((check) => check.id === "buggy-failure-is-assertion")?.passed).toBe(expected);
		expect(grade.passed).toBe(expected);
	});

	it("rejects timeout and unconditional or hidden-answer probes", () => {
		const timedOut = evaluateRealCodeGrade({
			generatedFiles: ["testing/agent_generated/test_behavior.py"],
			unexpectedChanges: [],
			suspiciousPatterns: [],
			buggyRun: processResult(1, "AssertionError", true),
			fixedRun: processResult(0),
			regressionRun: processResult(0),
		});
		expect(timedOut.passed).toBe(false);
		expect(
			findSuspiciousPatterns([
				"assert False\npytest.skip('not today')\nPath('gold.patch').read_text()\nsubprocess.run(['git', 'show'])",
			]),
		).toEqual([
			"git-metadata-probe",
			"hidden-evaluator-probe",
			"source-text-probe",
			"unconditional-failure",
			"unconditional-skip",
		]);
	});

	it("rejects missing tests, product edits, and source-probing tests", () => {
		const grade = evaluateRealCodeGrade({
			generatedFiles: [],
			generatedTests: [],
			unexpectedChanges: [" M src/_pytest/logging.py"],
			suspiciousPatterns: ["source-text-probe"],
			buggyRun: processResult(1, "FAILED test_behavior.py::test_contract - AssertionError"),
			fixedRun: processResult(0),
			regressionRun: processResult(0),
		});
		expect(grade.passed).toBe(false);
		expect(grade.checks.filter((check) => !check.passed).map((check) => check.id)).toEqual([
			"generated-tests-present",
			"product-source-unchanged",
			"anti-cheat-static-scan",
		]);
	});

	it("does not treat a conditional pytest.fail call as an unconditional failure", () => {
		expect(
			findSuspiciousPatterns([
				"try:\n    run_target()\nexcept ValueError as error:\n    pytest.fail(f'target failed: {error}')",
			]),
		).not.toContain("unconditional-failure");
	});

	it("returns independent task copies", () => {
		const task = getRealCodeTask("real-pytest-10051");
		task.title = "changed";
		expect(getRealCodeTask("real-pytest-10051").title).not.toBe("changed");
	});

	it("maps two development discovery tasks onto frozen environments", () => {
		const tasks = getRealCodeDiscoveryTasks();
		expect(tasks.map((task) => task.id)).toEqual(["discover-sphinx-10323", "discover-django-16454"]);
		for (const task of tasks) {
			expect(task.mode).toBe("discovery");
			expect(task.split).toBe("development");
			const source = getRealCodeTask(task.sourceTaskId ?? "");
			expect(task.instanceId).toBe(source.instanceId);
			expect(task.baseCommit).toBe(source.baseCommit);
			expect(task.image).toBe(source.image);
			expect(task.goldPatchPath).toBe(source.goldPatchPath);
			expect(task.upstreamTestPatchPath).toBe(source.upstreamTestPatchPath);
			expect(task.hiddenTestCommand).toBe(source.hiddenTestCommand);
		}
	});

	it("keeps discovery prompts free of known-defect instructions", () => {
		for (const task of getRealCodeDiscoveryTasks()) {
			const combined = `${task.prompt}\n${realCodeSystemPrompt(task)}`;
			expect(combined).not.toMatch(/reported regression|known buggy commit|hidden maintainer fix/i);
			expect(combined).toMatch(/plan_create/);
			expect(combined).toMatch(/budget of 12 repository/);
			expect(combined).toMatch(/smallest defensible test/);
		}
		expect(getRealCodeDiscoveryTask("discover-sphinx-10323").prompt).not.toMatch(
			/dedent_filter|synthetic boundary|9-11/i,
		);
		expect(getRealCodeDiscoveryTask("discover-django-16454").prompt).not.toMatch(
			/CommandError|invalid int value|two-line usage/i,
		);
	});

	it("moves discovery from bounded exploration to executable tests", async () => {
		const plans = new PlanStore();
		const trace = new TraceCollector();
		const policy = new RealCodeToolPolicy(plans, trace, true);
		const call = (name: string, id: string) => policy.beforeToolCall({ toolCall: { id, name } } as never);

		expect(await call("repo_read", "without-plan")).toMatchObject({ block: true });
		plans.create("Explore a feature contract", ["Inspect risks", "Write and run a focused test"]);
		for (let index = 0; index < 12; index += 1) {
			expect(await call("repo_read", `read-${index}`)).toBeUndefined();
		}
		expect(await call("repo_search", "read-13")).toMatchObject({
			block: true,
			reason: expect.stringContaining("exploration budget exhausted"),
		});
		expect(await call("test_write", "first-test")).toBeUndefined();
		expect(await call("repo_read", "after-test")).toBeUndefined();
	});

	it("returns independent discovery task copies", () => {
		const task = getRealCodeDiscoveryTask("discover-sphinx-10323");
		task.prompt = "changed";
		expect(getRealCodeDiscoveryTask("discover-sphinx-10323").prompt).not.toBe("changed");
	});

	it("defines four neutral development work orders for the agent-task protocol", () => {
		const tasks = getAgentTasks();
		expect(tasks).toHaveLength(4);
		for (const task of tasks) {
			expect(task.mode).toBe("agent-task");
			expect(task.split).toBe("development");
			expect(task.sourceTaskId).toBeTruthy();
			expect(task.prompt).toMatch(/Objective:/);
			expect(task.prompt).toMatch(/Requirements:/);
			expect(task.prompt).toMatch(/Deliverables:/);
			expect(`${task.prompt}\n${realCodeSystemPrompt(task)}`).not.toMatch(
				/act as|you are (?:a|an) .*testing|testing engineer|known buggy commit/i,
			);
			expect(realCodeRunLimits(task)).toEqual({ maxTurns: 64, maxWallTimeMs: 1_200_000 });
		}
	});

	it("returns independent agent-task copies", () => {
		const task = getAgentTask("agent-dev-pytest-caplog");
		task.prompt = "changed";
		expect(getAgentTask("agent-dev-pytest-caplog").prompt).not.toBe("changed");
	});

	it("classifies provider errors and evaluator limits separately", () => {
		expect(
			classifyTermination({
				wallTimeLimitReached: false,
				turnLimitReached: false,
				lastAssistantStopReason: "error",
			}),
		).toBe("runtime-error");
		expect(
			classifyTermination({
				wallTimeLimitReached: false,
				turnLimitReached: true,
				lastAssistantStopReason: "toolUse",
			}),
		).toBe("turn-limit");
		expect(
			classifyTermination({
				wallTimeLimitReached: true,
				turnLimitReached: true,
				lastAssistantStopReason: "aborted",
			}),
		).toBe("wall-time-limit");
	});

	it("defines development and staged evaluation issue tasks without exposing evaluator assets", () => {
		const tasks = getIssueTasks();
		expect(tasks.map((task) => task.id)).toEqual([
			"issue-dev-pytest-10051",
			"issue-dev-pytest-7490",
			"issue-dev-pylint-7080",
			"issue-dev-pylint-6903",
			"issue-eval-astropy-12907",
			"issue-eval-django-11141",
			"issue-eval-sklearn-13142",
			"issue-eval-sphinx-10449",
			"issue-eval-matplotlib-23412",
			"issue-eval-requests-1142",
			"issue-eval-xarray-3677",
			"issue-eval-pytest-8399",
			"issue-eval-astropy-13453",
			"issue-eval-django-11133",
			"issue-eval-sklearn-13328",
			"issue-eval-sympy-11618",
		]);
		for (const task of tasks) {
			expect(task.prompt).toMatch(/Resolve the reported issue/);
			expect(task.prompt).toMatch(/implement the necessary change/);
			expect(task.prompt).not.toMatch(/gold patch|upstream-tests|hidden test|maintainer patch|fixed workspace/i);
		}
		for (const task of tasks.filter((candidate) => candidate.split === "development")) {
			const source = getRealCodeTask(task.sourceTaskId);
			expect(task.instanceId).toBe(source.instanceId);
			expect(task.baseCommit).toBe(source.baseCommit);
			expect(task.image).toBe(source.image);
			expect(task.goldPatchPath).toBe(source.goldPatchPath);
			expect(task.upstreamTestPatchPath).toBe(source.upstreamTestPatchPath);
		}
		for (const task of tasks.filter((candidate) => candidate.split !== "development")) {
			expect(task.toolSmokeArgv?.length).toBeGreaterThan(2);
			expect(() => validateIssueCommand(task.toolSmokeArgv ?? [])).not.toThrow();
		}
		expect(tasks.filter((task) => task.split === "evaluation-core")).toHaveLength(4);
		expect(tasks.filter((task) => task.split === "evaluation-expansion-a")).toHaveLength(4);
		expect(tasks.filter((task) => task.split === "evaluation-expansion-b")).toHaveLength(4);
		expect(issueAgentSystemPrompt()).toMatch(/Product source may be modified/);
		expect(issueAgentSystemPrompt()).not.toMatch(/testing engineer|you are (?:a|an) engineer/i);
	});

	it("returns independent issue task copies", () => {
		const task = getIssueTask("issue-dev-pytest-10051");
		task.prompt = "changed";
		expect(getIssueTask("issue-dev-pytest-10051").prompt).not.toBe("changed");
	});

	it("freezes issue inputs, evaluator assets, and experiment conditions in a manifest", async () => {
		const expected: Record<string, [string, string, string, string, string]> = {
			"issue-dev-pytest-10051": [
				"87b2ad035a5414b7c51d45e3e788097e13f9f8fb003787b2a3ba8667175517fd",
				"938af8056b80ed8feaa6f36b56d4e702881d0e3f8115b52d470cc05b0c6a6023",
				"9662c12536a07d0cc5a6ce6f781edb31ea013af4df0e156522ca624aaed634c0",
				"c25a4ee0d66c1dc4c219f3bd54a0f9b7a9d96513d2d3a42cecb050b7182f3557",
				"f3c79340a770b5389fd4a8c196db794ba91d785f3c9308e4139e275f3d02bc75",
			],
			"issue-dev-pytest-7490": [
				"495549d4ae2f3b54cb75900702c749d7a36b333325b15232594fcbe7a59f04cd",
				"920557aa65bbd9a32a2e16482a60746b6010f7759601a605ba58c61dab3aeadb",
				"88a7af7e123619306d887c6a9bd1f905acc4872b454df914094c038b71356e30",
				"fe5323cfe9d6be9648be22ffb13a95079c6e17c13f09987f4c1b8589d27c690c",
				"ddbcefbfa291d4b4ee5b2faad30e21f904a19deebef6437ea202d7f9fcfd154c",
			],
			"issue-dev-pylint-7080": [
				"30cb9f7fe3c5d922d68c6e35aa4e212495f1458d7fb406a60030de5fa29c700d",
				"14ff8ffc48314faa8124a4d95facac94f292d4c7126398757097801f3db18383",
				"f908ad1f6d4b8df3755a634c6741abf8ae613bc59278a54c16560c16d255580f",
				"7d42b9582d23667623d84307132d46e63588cbcfe63c35bccb7a52940bf5ca08",
				"cfcecba4e30804ca3ffe8634783827e5f754df0c7571baa7f3fcaed6bc41b8a6",
			],
			"issue-dev-pylint-6903": [
				"fe9e1f13eae88f1758dbf21abcc7efbce9b533f1fc123bc7bbd5ebe6bf68853a",
				"994d2287134d53bef8e85df1492c037e5367f012d83c5d7a57c02afad573960f",
				"4b0aa53db6637dcaab2ff2373a8ebf3495df12181640b7dd38e5bb19c0e9fbcd",
				"e69b5e44c833af7f8d83cc91d97fe7bf7ac47ac1c24bfa8ab2a3ee47cd60121a",
				"51503506c9edb55d3a05a308b0181fd9026729b9483f4ef61dcb76facccb5d15",
			],
		};
		for (const task of getIssueTasks().filter((candidate) => candidate.split === "development")) {
			const manifest = await buildIssueFrozenManifest(task, {
				maxTurns: 96,
				maxWallTimeMs: 1_200_000,
				commandTimeoutMs: 180_000,
			});
			expect([
				manifest.hashes.issue,
				manifest.hashes.prompt,
				manifest.hashes.goldPatch,
				manifest.hashes.testPatch,
				manifest.hashes.commands,
			]).toEqual(expected[task.id]);
			expect(manifest.protocolVersion).toBe("issue-agent-eval-v1.0-dev");
			expect(manifest.experiment).toMatchObject({
				maxTurns: 96,
				maxWallTimeMs: 1_200_000,
				commandTimeoutMs: 180_000,
				containerCpus: 2,
				containerMemoryMiB: 4096,
				containerPidsLimit: 512,
				containerNetwork: "none",
				toolExecution: "sequential",
				modelContextWindow: 1_000_000,
				modelMaxOutputTokens: 32_768,
			});
			for (const hash of Object.values(manifest.hashes)) expect(hash).toMatch(/^[a-f0-9]{64}$/);
		}

		const evaluationAssets: Record<string, [string, string]> = {
			"issue-eval-astropy-12907": [
				"0f3e44432ed8540e9526edff4f83793948a2f139fc3971b67c30043c1eb7964a",
				"5ef90b640ffce4590bb61ef2ea0e3256416dddf41b45bf4f2c3610a6e8c53718",
			],
			"issue-eval-django-11141": [
				"924503df37ffda93f8c76bb1d615f2c5cd8a16f914bd76d63e9072de676514ee",
				"2368754a38055f10014bbcf18e23e95bbe26b0785de064f4fc561f6c3a03749f",
			],
			"issue-eval-sklearn-13142": [
				"41e34423abeef374c7f8dcb063cf3b4e8f33b90c2763477917a08b2bea9e6c18",
				"fae41fef7d46d2b4cff64ffffccee1b830a5d34c070874614913caa6e5430740",
			],
			"issue-eval-sphinx-10449": [
				"686d63a8c9b8ee171de780e334fa9ec3a84bbf0d88deb0fa5533cd816282da74",
				"d91618e097a8fc4fbe5563a018167bb677636de2b3d2d1a5e73912ab06ec68db",
			],
			"issue-eval-matplotlib-23412": [
				"3352ca35f5f8e588f75b3a826f7e4f6f858fdad6abc7f6f74618d38f4ebdf51b",
				"ce66c257f0b0a1493b8aec1ebd595deb57a38a1b4ddbdc92d40d34c05b2140eb",
			],
			"issue-eval-requests-1142": [
				"c25c1f930edaf8514d3a5f2bc5631a8e8080ecb2511c789ec171f37d14e627a6",
				"6671e023a6f6ed84969327ccee025cb8ab81e79c9477a7d635640c0b03836769",
			],
			"issue-eval-xarray-3677": [
				"eb6bc3dcb4a4406f23abc17190cffd828ee58e1948e5621cc75abbd9fae142cd",
				"378d54dd2ff404fe064e1dff3d69bd6cd2682f33b0e1202c664842b73d3372be",
			],
			"issue-eval-pytest-8399": [
				"fcc0962e001dbc5c6c718a885b0a3369af3b018b942d3574675c51ecdc59eff2",
				"506cca658cf270e5ef62256ea4261cd6557ed8368317a97d715123486fa1ddd6",
			],
			"issue-eval-astropy-13453": [
				"7f579408e4ad94b129886283f9c6df7d27d3965e2aaaa88141045515dd4710d9",
				"be7129ea12a3abcbf2b09882ef29e32376c84c7b2e94a08664df0e87a3b88597",
			],
			"issue-eval-django-11133": [
				"a0fd6d0a4a1c733a1c6f98cbb524604ea24e643c26c5fcb152be4bfb29fb1c3d",
				"ef28eb84141ad909c3732fc7473368f1c6a22510e9ed51190401876db8e003f9",
			],
			"issue-eval-sklearn-13328": [
				"4d43cadc3e1bd7101beadddb99609e524b44d28394286e7bca67692cd4a14d68",
				"ea0bc13b412ab4f474800827ddd44eddb8ab926782c7a143c330cb84effeec95",
			],
			"issue-eval-sympy-11618": [
				"2dcab4569a942fedbfd80ffa3d8b0a97f03a7b806d35698824dc38e767ebec84",
				"9db55dd61b4ad7c82838f96a1383f8b182a59e13a31ebc9bdc12e27e7bea1d36",
			],
		};
		for (const task of getIssueTasks().filter((candidate) => candidate.split !== "development")) {
			const manifest = await buildIssueFrozenManifest(task, {
				maxTurns: 96,
				maxWallTimeMs: 1_200_000,
				commandTimeoutMs: 180_000,
			});
			expect([manifest.hashes.goldPatch, manifest.hashes.testPatch]).toEqual(evaluationAssets[task.id]);
			expect(manifest.protocolVersion).toBe("issue-agent-eval-v1.0.2");
			for (const hash of Object.values(manifest.hashes)) expect(hash).toMatch(/^[a-f0-9]{64}$/);
		}
	});

	it("freezes OpenCode Go GLM as a model-comparison runtime", async () => {
		expect(normalizeAgentEvalProvider("opencodego")).toBe("opencode-go");
		expect(normalizeOpenAIBaseUrl("https://opencode.ai/zen/go/v1/chat/completions")).toBe(
			"https://opencode.ai/zen/go/v1",
		);
		const model = {
			provider: "opencode-go",
			id: "glm-5.2",
			api: "openai-completions",
			baseUrl: "https://opencode.ai/zen/go/v1",
			contextWindow: 1_000_000,
			maxTokens: 32_768,
		};
		const manifest = await buildIssueFrozenManifest(
			getIssueTask("issue-eval-astropy-12907"),
			{ maxTurns: 96, maxWallTimeMs: 1_200_000, commandTimeoutMs: 180_000 },
			model,
		);
		expect(manifest.protocolVersion).toBe("issue-agent-eval-v1.1-model-comparison");
		expect(manifest.experiment).toMatchObject({
			modelProvider: "opencode-go",
			modelId: "glm-5.2",
			modelApi: "openai-completions",
			modelBaseUrl: "https://opencode.ai/zen/go/v1",
			modelContextWindow: 1_000_000,
			modelMaxOutputTokens: 32_768,
		});
	});

	it("protects Git metadata, existing test paths, lock files, and arbitrary commands", () => {
		expect(isRepositoryTestPath("testing/logging/test_fixture.py")).toBe(true);
		expect(isRepositoryTestPath("src/_pytest/logging.py")).toBe(false);
		expect(isDependencyLockPath("uv.lock")).toBe(true);
		expect(isDependencyLockPath("src/lock.py")).toBe(false);
		expect(validateIssueCommand(["pytest", "-q", "testing/logging/test_fixture.py"])).toEqual([
			"/opt/miniconda3/envs/testbed/bin/python",
			"-m",
			"pytest",
			"-q",
			"testing/logging/test_fixture.py",
		]);
		expect(validateIssueCommand(["python", "-m", "pytest", "-q"])).toEqual([
			"/opt/miniconda3/envs/testbed/bin/python",
			"-m",
			"pytest",
			"-q",
		]);
		expect(() => validateIssueCommand(["bash", "-lc", "pytest -q"])).toThrow("Only Python and pytest");
		expect(() => validateIssueCommand(["python", "-c", "print(1)"])).toThrow("Inline Python");
		expect(() => validateIssueCommand(["pytest", ".git/config"])).toThrow("protected evaluator or Git");
		expect(() => validateIssueCommand(["pytest", "/tmp/test.py"])).toThrow("Absolute command paths");
		expect(validateIssueDeletePath("testing/test_attempt_probe.py", false)).toBe("testing/test_attempt_probe.py");
		expect(() => validateIssueDeletePath("src/_pytest/logging.py", true)).toThrow(
			"Tracked repository files cannot be deleted",
		);
		expect(() => validateIssueDeletePath("uv.lock", false)).toThrow("Dependency lock files are read-only");
		expect(() => validateIssueDeletePath(".git/config", false)).toThrow("Git metadata");
		expect(buildIssueAgentTestCommand(getIssueTask("issue-dev-pytest-10051"), ["testing/test_attempt.py"])).toBe(
			"pytest -q 'testing/test_attempt.py'",
		);
		expect(
			buildIssueAgentTestCommand(getIssueTask("issue-eval-django-11141"), [
				"tests/agent_generated/test_namespace.py",
			]),
		).toBe("python tests/runtests.py 'agent_generated.test_namespace'");
	});

	it("passes issue grading only with product changes, injected target tests, and safe regression results", () => {
		const grade = evaluateIssueGrade({
			repositoryStatus: " M src/_pytest/logging.py\n",
			finalPatch: "diff --git a/src/_pytest/logging.py b/src/_pytest/logging.py",
			changedFiles: [changedFile("src/_pytest/logging.py")],
			forbiddenChanges: [],
			productChangeCount: 1,
			evaluatorSetupRun: processResult(0),
			agentTestsRun: processResult(0),
			targetRun: processResult(0),
			regressionRun: processResult(0),
		});
		expect(grade.passed).toBe(true);
		expect(grade.checks.every((check) => check.passed)).toBe(true);
	});

	it("keeps issue grader failures attributable to their individual checks", () => {
		const grade = evaluateIssueGrade({
			repositoryStatus: " M testing/test_feature.py\n",
			finalPatch: "",
			changedFiles: [changedFile("testing/test_feature.py")],
			forbiddenChanges: [" M testing/test_feature.py"],
			productChangeCount: 0,
			evaluatorSetupRun: processResult(1),
			agentTestsRun: processResult(1),
			targetRun: processResult(125),
			regressionRun: processResult(1),
		});
		expect(grade.passed).toBe(false);
		expect(grade.checks.filter((check) => !check.passed).map((check) => check.id)).toEqual([
			"product-change-present",
			"protected-files-unchanged",
			"agent-added-tests-pass",
			"hidden-tests-injected",
			"target-behavior-resolved",
			"related-regression-safe",
		]);
	});

	it("allows infrastructure retry only for provider-shaped errors", () => {
		expect(isProviderInfrastructureError("fetch failed: connection reset by peer")).toBe(true);
		expect(isProviderInfrastructureError("429 rate limit exceeded")).toBe(true);
		expect(isProviderInfrastructureError("repo_write rejected an existing test file")).toBe(false);
	});
});
