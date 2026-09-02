#!/usr/bin/env node
import { resolve } from "node:path";
import { loadAgentEvalConfig, loadAgentEvalProviderConfig, redactedConfig } from "./config.ts";
import { readResults, writeResultArtifacts } from "./eval/artifacts.ts";
import { evaluateTask } from "./eval/evaluator.ts";
import { runHttpTask } from "./eval/http-runner.ts";
import { runOnlineTask } from "./eval/online-runner.ts";
import { renderMarkdownReport, summarize } from "./eval/report.ts";
import { runOfflineSuite, runOfflineTask } from "./eval/runner.ts";
import { createDeepSeekRuntime, createAgentEvalRuntime } from "./model/deepseek.ts";
import type { PolicyProfile } from "./policy/policy.ts";
import { getAgentTask, getAgentTasks } from "./real-code/agent-tasks.ts";
import { renderRealCodePreflight, renderRealCodeReport, writeRealCodeArtifacts } from "./real-code/artifacts.ts";
import { getRealCodeDiscoveryTask, getRealCodeDiscoveryTasks } from "./real-code/discovery-tasks.ts";
import {
	renderIssuePreflight,
	renderIssueReport,
	renderIssueToolSmoke,
	writeIssueArtifacts,
	writeIssueManifest,
} from "./real-code/issue-artifacts.ts";
import { buildIssueFrozenManifest } from "./real-code/issue-manifest.ts";
import { runIssuePreflight, runIssueTask, runIssueToolSmoke } from "./real-code/issue-runner.ts";
import { getIssueTask, getIssueTasks } from "./real-code/issue-tasks.ts";
import { runNanoCursorIssueTask, validateNanoCursorCandidate } from "./real-code/nanocursor-runner.ts";
import { runRealCodePreflight, runRealCodeTask } from "./real-code/runner.ts";
import { getRealCodeTask, getRealCodeTasks } from "./real-code/tasks.ts";
import { getTask, getTaskCatalog, type TaskSuite, validateTaskCatalog } from "./tasks/catalog.ts";
import type { EvalResult } from "./types.ts";

function option(args: string[], name: string): string | undefined {
	const index = args.indexOf(name);
	return index >= 0 ? args[index + 1] : undefined;
}

function timestamp(): string {
	return new Date().toISOString().replace(/[:.]/g, "-");
}

function defaultOutput(label: string): string {
	return resolve(".artifacts", "agent-eval", `${label}-${timestamp()}.json`);
}

function usage(): string {
	return `AgentEval

Commands:
  validate
  list [--suite baseline|hard|hard-v2|collab|collab-heldout|collab-heldout-v1|collab-heldout-v1.1|collab-regression-v1.1|collab-final-v2|all]
  run <task-id> [--model offline|deepseek] [--sandbox memory|http] [--judge none|model] [--sandbox-url url] [--policy strict-active|plan-required] [--env path] [--out path]
  eval [--model offline|deepseek] [--sandbox memory|http] [--judge none|model] [--sandbox-url url] [--policy strict-active|plan-required] [--suite baseline|hard|hard-v2|collab|collab-heldout|collab-heldout-v1|collab-heldout-v1.1|collab-regression-v1.1|collab-final-v2|all] [--tasks id,id] [--limit number] [--trials number] [--env path] [--out path]
  report <results.json>
  regrade <results.json> [--out path]
  replay <results.json> [task-id]
  compare <baseline.json> <candidate.json>
  real-list
  real-preflight <task-id> [--docker-host url] [--out path]
  real-run <task-id> [--model deepseek] [--docker-host url] [--trials number] [--env path] [--out path]
  real-discovery-list
  real-discovery-run <task-id> [--model deepseek] [--docker-host url] [--trials number] [--env path] [--out path]
  agent-task-list
  agent-task-preflight <task-id> [--docker-host url] [--out path]
  agent-task-run <task-id> [--model deepseek] [--docker-host url] [--trials number] [--trial-index number] [--max-turns number] [--max-wall-ms number] [--env path] [--out path]
  issue-list
  issue-manifest <task-id> [--model deepseek|opencode-go] [--env path] [--out path]
  issue-smoke <task-id> [--docker-host url] [--out path]
  issue-preflight <task-id> [--docker-host url] [--out path]
  issue-run <task-id> [--model deepseek|opencode-go] [--docker-host url] [--trials number] [--trial-index number] [--attempt-index number] [--max-turns number] [--max-wall-ms number] [--env path] [--out path]
  issue-nanocursor-validate --nanocursor-root path
  issue-nanocursor-run <task-id> --nanocursor-root path [--model deepseek|opencode-go] [--docker-host url] [--trials number] [--trial-index number] [--max-turns number] [--max-wall-ms number] [--env path] [--out path]`;
}

function sandboxMode(args: string[]): "memory" | "http" {
	const value = option(args, "--sandbox") ?? "memory";
	if (value !== "memory" && value !== "http") throw new Error(`Unsupported sandbox mode: ${value}`);
	return value;
}

function sandboxUrl(args: string[]): string {
	return option(args, "--sandbox-url") ?? "http://127.0.0.1:8100";
}

function runModelReview(args: string[], sandbox: "memory" | "http"): boolean {
	const value = option(args, "--judge") ?? "none";
	if (value !== "none" && value !== "model") throw new Error(`Unsupported judge mode: ${value}`);
	if (value === "model" && sandbox !== "http") throw new Error("--judge model requires --sandbox http.");
	return value === "model";
}

function taskSuite(args: string[]): TaskSuite {
	const value = option(args, "--suite") ?? "baseline";
	if (
		value !== "baseline" &&
		value !== "hard" &&
		value !== "hard-v2" &&
		value !== "collab" &&
		value !== "collab-heldout" &&
		value !== "collab-heldout-v1" &&
		value !== "collab-heldout-v1.1" &&
		value !== "collab-regression-v1.1" &&
		value !== "collab-final-v2" &&
		value !== "all"
	) {
		throw new Error(`Unsupported task suite: ${value}`);
	}
	return value;
}

function policyProfile(args: string[]): PolicyProfile {
	const value = option(args, "--policy") ?? "strict-active";
	if (value !== "strict-active" && value !== "plan-required") {
		throw new Error(`Unsupported policy profile: ${value}`);
	}
	return value;
}

function realCodeDockerHost(args: string[]): string | undefined {
	return option(args, "--docker-host") ?? process.env.AGENT_EVAL_DOCKER_HOST;
}

function realCodeCommandTimeout(args: string[]): number {
	const value = Number.parseInt(option(args, "--command-timeout-ms") ?? "180000", 10);
	if (!Number.isInteger(value) || value < 10_000) throw new Error("--command-timeout-ms must be at least 10000.");
	return value;
}

async function runRealPreflightCommand(args: string[]): Promise<void> {
	const taskId = args[0];
	if (!taskId) throw new Error("real-preflight requires a task id.");
	const task = getRealCodeTask(taskId);
	const result = await runRealCodePreflight(task, {
		dockerHost: realCodeDockerHost(args),
		commandTimeoutMs: realCodeCommandTimeout(args),
	});
	const output = await writeRealCodeArtifacts(
		[result],
		option(args, "--out") ?? defaultOutput(`${task.id}-preflight`),
	);
	console.log(renderRealCodePreflight(result));
	console.log(`Artifacts: ${output}`);
	if (!result.passed) process.exitCode = 2;
}

async function runIssuePreflightCommand(args: string[]): Promise<void> {
	const taskId = args[0];
	if (!taskId) throw new Error("issue-preflight requires a task id.");
	const task = getIssueTask(taskId);
	const result = await runIssuePreflight(task, {
		dockerHost: realCodeDockerHost(args),
		commandTimeoutMs: realCodeCommandTimeout(args),
	});
	const output = await writeIssueArtifacts(
		[result],
		option(args, "--out") ?? defaultOutput(`${task.id}-issue-preflight`),
	);
	console.log(renderIssuePreflight(result));
	console.log(`Artifacts: ${output}`);
	if (!result.passed) process.exitCode = 2;
}

async function runIssueSmokeCommand(args: string[]): Promise<void> {
	const taskId = args[0];
	if (!taskId) throw new Error("issue-smoke requires a task id.");
	const task = getIssueTask(taskId);
	const result = await runIssueToolSmoke(task, {
		dockerHost: realCodeDockerHost(args),
		commandTimeoutMs: realCodeCommandTimeout(args),
	});
	const output = await writeIssueArtifacts([result], option(args, "--out") ?? defaultOutput(`${task.id}-issue-smoke`));
	console.log(renderIssueToolSmoke(result));
	console.log(`Artifacts: ${output}`);
	if (!result.passed) process.exitCode = 2;
}

async function runIssueTaskCommand(args: string[]): Promise<void> {
	const taskId = args[0];
	if (!taskId) throw new Error("issue-run requires a task id.");
	const model = option(args, "--model") ?? "deepseek";
	if (model !== "deepseek" && model !== "opencode-go") {
		throw new Error("issue-run supports --model deepseek or --model opencode-go.");
	}
	const trials = Number.parseInt(option(args, "--trials") ?? "1", 10);
	const firstTrialIndex = Number.parseInt(option(args, "--trial-index") ?? "1", 10);
	const firstAttemptIndex = Number.parseInt(option(args, "--attempt-index") ?? "1", 10);
	const maxTurns = Number.parseInt(option(args, "--max-turns") ?? "96", 10);
	const maxWallTimeMs = Number.parseInt(option(args, "--max-wall-ms") ?? "1200000", 10);
	if (!Number.isInteger(trials) || trials < 1) throw new Error("--trials must be a positive integer.");
	if (!Number.isInteger(firstTrialIndex) || firstTrialIndex < 1) {
		throw new Error("--trial-index must be a positive integer.");
	}
	if (!Number.isInteger(firstAttemptIndex) || firstAttemptIndex < 1) {
		throw new Error("--attempt-index must be a positive integer.");
	}
	if (option(args, "--attempt-index") && trials !== 1) {
		throw new Error("--attempt-index can only be used with --trials 1.");
	}
	if (!Number.isInteger(maxTurns) || maxTurns < 1) throw new Error("--max-turns must be a positive integer.");
	if (!Number.isInteger(maxWallTimeMs) || maxWallTimeMs < 10_000) {
		throw new Error("--max-wall-ms must be at least 10000.");
	}
	const config = await loadAgentEvalProviderConfig(option(args, "--env"), model);
	console.log(`Model configuration: ${JSON.stringify(redactedConfig(config))}`);
	const runtime = createAgentEvalRuntime(config);
	const task = getIssueTask(taskId);
	const attempts = [];
	const finalResults = [];
	for (let offset = 0; offset < trials; offset += 1) {
		const trialIndex = firstTrialIndex + offset;
		const attemptIndex = option(args, "--attempt-index") ? firstAttemptIndex : 1;
		console.log(`Running issue ${task.id} trial ${trialIndex}, attempt ${attemptIndex}: ${task.title}`);
		let result = await runIssueTask(task, runtime, {
			dockerHost: realCodeDockerHost(args),
			commandTimeoutMs: realCodeCommandTimeout(args),
			trialIndex,
			attemptIndex,
			maxTurns,
			maxWallTimeMs,
		});
		attempts.push(result);
		if (result.providerRetryEligible) {
			console.log(`Retrying issue ${task.id} trial ${trialIndex} after a pre-action Provider failure.`);
			result = await runIssueTask(task, runtime, {
				dockerHost: realCodeDockerHost(args),
				commandTimeoutMs: realCodeCommandTimeout(args),
				trialIndex,
				attemptIndex: attemptIndex + 1,
				maxTurns,
				maxWallTimeMs,
			});
			attempts.push(result);
		}
		finalResults.push(result);
	}
	const output = await writeIssueArtifacts(
		attempts,
		option(args, "--out") ?? defaultOutput(`${task.id}-${model}-issue`),
	);
	console.log(renderIssueReport(attempts));
	console.log(`Artifacts: ${output}`);
	if (finalResults.some((result) => !result.passed)) process.exitCode = 2;
}

async function runNanoCursorIssueTaskCommand(args: string[]): Promise<void> {
	const taskId = args[0];
	if (!taskId) throw new Error("issue-nanocursor-run requires a task id.");
	const nanoCursorRoot = option(args, "--nanocursor-root");
	if (!nanoCursorRoot) throw new Error("issue-nanocursor-run requires --nanocursor-root.");
	const model = option(args, "--model") ?? "deepseek";
	if (model !== "deepseek" && model !== "opencode-go") {
		throw new Error("issue-nanocursor-run supports --model deepseek or --model opencode-go.");
	}
	const trials = Number.parseInt(option(args, "--trials") ?? "1", 10);
	const firstTrialIndex = Number.parseInt(option(args, "--trial-index") ?? "1", 10);
	const maxTurns = Number.parseInt(option(args, "--max-turns") ?? "96", 10);
	const maxWallTimeMs = Number.parseInt(option(args, "--max-wall-ms") ?? "1200000", 10);
	if (!Number.isInteger(trials) || trials < 1) throw new Error("--trials must be a positive integer.");
	if (!Number.isInteger(firstTrialIndex) || firstTrialIndex < 1) {
		throw new Error("--trial-index must be a positive integer.");
	}
	if (!Number.isInteger(maxTurns) || maxTurns < 1) throw new Error("--max-turns must be a positive integer.");
	if (!Number.isInteger(maxWallTimeMs) || maxWallTimeMs < 10_000) {
		throw new Error("--max-wall-ms must be at least 10000.");
	}
	const config = await loadAgentEvalProviderConfig(option(args, "--env"), model);
	console.log(`Model configuration: ${JSON.stringify(redactedConfig(config))}`);
	const task = getIssueTask(taskId);
	const results = [];
	for (let offset = 0; offset < trials; offset += 1) {
		const trialIndex = firstTrialIndex + offset;
		console.log(`Running NanoCursor N0 issue ${task.id} trial ${trialIndex}: ${task.title}`);
		results.push(
			await runNanoCursorIssueTask(task, config, {
				nanoCursorRoot,
				dockerHost: realCodeDockerHost(args),
				commandTimeoutMs: realCodeCommandTimeout(args),
				trialIndex,
				maxTurns,
				maxWallTimeMs,
				...(option(args, "--nanocursor-artifacts")
					? { outputDirectory: option(args, "--nanocursor-artifacts") }
					: {}),
			}),
		);
	}
	const output = await writeIssueArtifacts(
		results,
		option(args, "--out") ?? defaultOutput(`${task.id}-${model}-nanocursor-issue`),
	);
	console.log(renderIssueReport(results));
	console.log(`Artifacts: ${output}`);
	if (results.some((result) => !result.passed)) process.exitCode = 2;
}

type RealTaskCatalog = "regression" | "discovery" | "agent-task";

function selectedRealTask(taskId: string, catalog: RealTaskCatalog) {
	if (catalog === "discovery") return getRealCodeDiscoveryTask(taskId);
	if (catalog === "agent-task") return getAgentTask(taskId);
	return getRealCodeTask(taskId);
}

async function runRealTaskCommand(args: string[], catalog: RealTaskCatalog = "regression"): Promise<void> {
	const taskId = args[0];
	const command =
		catalog === "agent-task" ? "agent-task-run" : catalog === "discovery" ? "real-discovery-run" : "real-run";
	if (!taskId) throw new Error(`${command} requires a task id.`);
	const model = option(args, "--model") ?? "deepseek";
	if (model !== "deepseek") throw new Error(`${command} currently supports only --model deepseek.`);
	const trials = Number.parseInt(option(args, "--trials") ?? "1", 10);
	if (!Number.isInteger(trials) || trials < 1) throw new Error("--trials must be a positive integer.");
	const config = await loadAgentEvalConfig(option(args, "--env"));
	console.log(`Model configuration: ${JSON.stringify(redactedConfig(config))}`);
	const runtime = createDeepSeekRuntime(config);
	const task = selectedRealTask(taskId, catalog);
	const maxTurns = Number.parseInt(option(args, "--max-turns") ?? "64", 10);
	const maxWallTimeMs = Number.parseInt(option(args, "--max-wall-ms") ?? "1200000", 10);
	const firstTrialIndex = Number.parseInt(option(args, "--trial-index") ?? "1", 10);
	if (catalog === "agent-task" && (!Number.isInteger(maxTurns) || maxTurns < 1)) {
		throw new Error("--max-turns must be a positive integer.");
	}
	if (catalog === "agent-task" && (!Number.isInteger(maxWallTimeMs) || maxWallTimeMs < 10_000)) {
		throw new Error("--max-wall-ms must be at least 10000.");
	}
	if (catalog === "agent-task" && (!Number.isInteger(firstTrialIndex) || firstTrialIndex < 1)) {
		throw new Error("--trial-index must be a positive integer.");
	}
	const results = [];
	for (let offset = 0; offset < trials; offset++) {
		const trialIndex = catalog === "agent-task" ? firstTrialIndex + offset : offset + 1;
		const finalTrialIndex = catalog === "agent-task" ? firstTrialIndex + trials - 1 : trials;
		console.log(`Running real-code ${task.id} trial ${trialIndex}/${finalTrialIndex}: ${task.title}`);
		results.push(
			await runRealCodeTask(task, runtime, {
				dockerHost: realCodeDockerHost(args),
				commandTimeoutMs: realCodeCommandTimeout(args),
				trialIndex,
				...(catalog === "agent-task"
					? {
							maxTurns,
							maxWallTimeMs,
						}
					: {}),
			}),
		);
	}
	const output = await writeRealCodeArtifacts(results, option(args, "--out") ?? defaultOutput(`${task.id}-${model}`));
	console.log(renderRealCodeReport(results));
	console.log(`Artifacts: ${output}`);
	if (results.some((result) => !result.passed)) process.exitCode = 2;
}

async function runOne(args: string[]): Promise<void> {
	const taskId = args[0];
	if (!taskId) throw new Error("run requires a task id.");
	const task = getTask(taskId);
	const model = option(args, "--model") ?? "offline";
	const policy = policyProfile(args);
	const sandbox = sandboxMode(args);
	const judge = runModelReview(args, sandbox);
	let result: EvalResult;
	if (sandbox === "http" && model === "offline") {
		result = await runHttpTask(task, { baseUrl: sandboxUrl(args), policyProfile: policy, runModelReview: judge });
	} else if (sandbox === "http" && model === "deepseek") {
		const config = await loadAgentEvalConfig(option(args, "--env"));
		console.log(`Model configuration: ${JSON.stringify(redactedConfig(config))}`);
		result = await runHttpTask(task, {
			baseUrl: sandboxUrl(args),
			policyProfile: policy,
			runModelReview: judge,
			runtime: createDeepSeekRuntime(config),
		});
	} else if (model === "offline") {
		result = await runOfflineTask(task, 1, policy);
	} else if (model === "deepseek") {
		const config = await loadAgentEvalConfig(option(args, "--env"));
		console.log(`Model configuration: ${JSON.stringify(redactedConfig(config))}`);
		result = await runOnlineTask(task, createDeepSeekRuntime(config), 1, policy);
	} else {
		throw new Error(`Unsupported model runtime: ${model}`);
	}
	const output = await writeResultArtifacts([result], option(args, "--out") ?? defaultOutput(taskId));
	console.log(renderMarkdownReport([result]));
	console.log(`Artifacts: ${output}`);
	if (!result.passed) process.exitCode = 2;
}

async function runSuite(args: string[]): Promise<void> {
	const catalog = getTaskCatalog(taskSuite(args));
	const requestedLimit = Number.parseInt(option(args, "--limit") ?? String(catalog.length), 10);
	if (!Number.isInteger(requestedLimit) || requestedLimit < 1) throw new Error("--limit must be a positive integer.");
	const trials = Number.parseInt(option(args, "--trials") ?? "1", 10);
	if (!Number.isInteger(trials) || trials < 1) throw new Error("--trials must be a positive integer.");
	const selectedTaskIds = option(args, "--tasks")
		?.split(",")
		.map((taskId) => taskId.trim())
		.filter((taskId) => taskId.length > 0);
	const tasks = selectedTaskIds?.length
		? selectedTaskIds.map((taskId) => getTask(taskId))
		: catalog.slice(0, requestedLimit);
	const model = option(args, "--model") ?? "offline";
	const policy = policyProfile(args);
	const sandbox = sandboxMode(args);
	const judge = runModelReview(args, sandbox);
	let results: EvalResult[];
	if (sandbox === "http") {
		if (tasks.some((task) => task.category !== "qa")) {
			throw new Error("The HTTP sandbox currently supports only collaboration suites or explicit qa task IDs.");
		}
		const runtime =
			model === "deepseek"
				? createDeepSeekRuntime(await loadAgentEvalConfig(option(args, "--env")))
				: model === "offline"
					? undefined
					: (() => {
							throw new Error(`Unsupported model runtime: ${model}`);
						})();
		results = [];
		for (const task of tasks) {
			for (let trialIndex = 1; trialIndex <= trials; trialIndex++) {
				console.log(`Running ${task.id} trial ${trialIndex}/${trials}: ${task.title}`);
				results.push(
					await runHttpTask(task, {
						baseUrl: sandboxUrl(args),
						trialIndex,
						policyProfile: policy,
						runModelReview: judge,
						...(runtime ? { runtime } : {}),
					}),
				);
			}
		}
	} else if (model === "offline") {
		results = await runOfflineSuite(tasks, trials, policy);
	} else if (model === "deepseek") {
		const config = await loadAgentEvalConfig(option(args, "--env"));
		console.log(`Model configuration: ${JSON.stringify(redactedConfig(config))}`);
		const runtime = createDeepSeekRuntime(config);
		results = [];
		for (const task of tasks) {
			for (let trialIndex = 1; trialIndex <= trials; trialIndex++) {
				console.log(`Running ${task.id} trial ${trialIndex}/${trials}: ${task.title}`);
				results.push(await runOnlineTask(task, runtime, trialIndex, policy));
			}
		}
	} else {
		throw new Error(`Unsupported model runtime: ${model}`);
	}
	const output = await writeResultArtifacts(results, option(args, "--out") ?? defaultOutput(`${model}-suite`));
	console.log(renderMarkdownReport(results));
	console.log(`Artifacts: ${output}`);
	if (results.some((result) => !result.passed)) process.exitCode = 2;
}

async function replay(args: string[]): Promise<void> {
	const path = args[0];
	if (!path) throw new Error("replay requires a result JSON path.");
	const requestedTask = args[1];
	const results = await readResults(path);
	const result = requestedTask ? results.find((candidate) => candidate.taskId === requestedTask) : results[0];
	if (!result)
		throw new Error(requestedTask ? `Task ${requestedTask} is not present in the artifact.` : "Artifact is empty.");
	console.log(`# Replay ${result.taskId}`);
	for (const event of result.trace) {
		if (
			event.type === "agent.tool_execution_start" ||
			event.type === "agent.tool_execution_end" ||
			event.type === "policy.decision" ||
			event.type === "run.error"
		) {
			console.log(`${event.sequence}\t${event.type}\t${JSON.stringify(event.payload)}`);
		}
	}
}

async function compare(args: string[]): Promise<void> {
	const baselinePath = args[0];
	const candidatePath = args[1];
	if (!baselinePath || !candidatePath) throw new Error("compare requires baseline and candidate JSON paths.");
	const baseline = summarize(await readResults(baselinePath));
	const candidate = summarize(await readResults(candidatePath));
	console.log(
		JSON.stringify(
			{
				baseline,
				candidate,
				delta: {
					passRate: candidate.passRate - baseline.passRate,
					taskStabilityRate: candidate.taskStabilityRate - baseline.taskStabilityRate,
					requiredToolRecall: candidate.averageRequiredToolRecall - baseline.averageRequiredToolRecall,
					toolPrecision: candidate.averageToolPrecision - baseline.averageToolPrecision,
					planCompletionRate: candidate.averagePlanCompletionRate - baseline.averagePlanCompletionRate,
					policyBlocks: candidate.policyBlocks - baseline.policyBlocks,
					toolErrors: candidate.toolErrors - baseline.toolErrors,
					totalTokens: candidate.totalTokens - baseline.totalTokens,
					averageDurationMs: candidate.averageDurationMs - baseline.averageDurationMs,
				},
			},
			null,
			2,
		),
	);
}

async function main(): Promise<void> {
	const [command, ...args] = process.argv.slice(2);
	if (!command || command === "help" || command === "--help") {
		console.log(usage());
		return;
	}
	if (command === "validate") {
		const errors = validateTaskCatalog();
		if (errors.length > 0) throw new Error(errors.join("\n"));
		console.log(
			`Validated ${getTaskCatalog("all").length} active tasks (${getTaskCatalog("baseline").length} baseline + ${getTaskCatalog("hard").length} hard v1 + ${getTaskCatalog("hard-v2").length} hard v2 + ${getTaskCatalog("collab").length} collaboration dev + ${getTaskCatalog("collab-regression-v1.1").length} collaboration regression v1.1 + ${getTaskCatalog("collab-final-v2").length} frozen final v2); archived heldout v1: ${getTaskCatalog("collab-heldout-v1").length}.`,
		);
		return;
	}
	if (command === "list") {
		for (const task of getTaskCatalog(taskSuite(args))) console.log(`${task.id}\t${task.category}\t${task.title}`);
		return;
	}
	if (command === "run") return runOne(args);
	if (command === "eval") return runSuite(args);
	if (command === "report") {
		const path = args[0];
		if (!path) throw new Error("report requires a result JSON path.");
		console.log(renderMarkdownReport(await readResults(path)));
		return;
	}
	if (command === "regrade") {
		const path = args[0];
		if (!path) throw new Error("regrade requires a result JSON path.");
		const previous = await readResults(path);
		const regraded = previous.map((result) =>
			evaluateTask(
				getTask(result.taskId),
				result.finalWorld,
				result.trace,
				result.finalPlan !== undefined,
				result.finalPlan,
				{
					trialIndex: result.trialIndex,
					runtime: result.runtime,
					model: result.model,
					policyProfile: result.policyProfile,
				},
			),
		);
		const output = await writeResultArtifacts(regraded, option(args, "--out") ?? defaultOutput("regraded"));
		console.log(renderMarkdownReport(regraded));
		console.log(`Artifacts: ${output}`);
		return;
	}
	if (command === "replay") return replay(args);
	if (command === "compare") return compare(args);
	if (command === "real-list") {
		for (const task of getRealCodeTasks()) {
			console.log(`${task.id}\t${task.split}\t${task.difficulty}\t${task.taskType}\t${task.title}`);
		}
		return;
	}
	if (command === "real-preflight") return runRealPreflightCommand(args);
	if (command === "real-run") return runRealTaskCommand(args);
	if (command === "real-discovery-list") {
		for (const task of getRealCodeDiscoveryTasks()) {
			console.log(`${task.id}\t${task.split}\t${task.difficulty}\t${task.title}`);
		}
		return;
	}
	if (command === "real-discovery-run") return runRealTaskCommand(args, "discovery");
	if (command === "agent-task-list") {
		for (const task of getAgentTasks()) {
			console.log(`${task.id}\t${task.split}\t${task.difficulty}\t${task.title}`);
		}
		return;
	}
	if (command === "agent-task-preflight") {
		const taskId = args[0];
		if (!taskId) throw new Error("agent-task-preflight requires a task id.");
		const task = getAgentTask(taskId);
		const result = await runRealCodePreflight(task, {
			dockerHost: realCodeDockerHost(args),
			commandTimeoutMs: realCodeCommandTimeout(args),
		});
		const output = await writeRealCodeArtifacts(
			[result],
			option(args, "--out") ?? defaultOutput(`${task.id}-preflight`),
		);
		console.log(renderRealCodePreflight(result));
		console.log(`Artifacts: ${output}`);
		if (!result.passed) process.exitCode = 2;
		return;
	}
	if (command === "agent-task-run") return runRealTaskCommand(args, "agent-task");
	if (command === "issue-list") {
		for (const task of getIssueTasks()) {
			console.log(`${task.id}\t${task.split}\t${task.difficulty}\t${task.instanceId}\t${task.title}`);
		}
		return;
	}
	if (command === "issue-manifest") {
		const taskId = args[0];
		if (!taskId) throw new Error("issue-manifest requires a task id.");
		const task = getIssueTask(taskId);
		const modelProvider = option(args, "--model");
		if (modelProvider && modelProvider !== "deepseek" && modelProvider !== "opencode-go") {
			throw new Error("issue-manifest supports --model deepseek or --model opencode-go.");
		}
		const runtime = modelProvider
			? createAgentEvalRuntime(await loadAgentEvalProviderConfig(option(args, "--env"), modelProvider))
			: undefined;
		const manifest = await buildIssueFrozenManifest(
			task,
			{
				maxTurns: 96,
				maxWallTimeMs: 1_200_000,
				commandTimeoutMs: realCodeCommandTimeout(args),
			},
			runtime?.model,
		);
		const output = await writeIssueManifest(manifest, option(args, "--out") ?? defaultOutput(`${task.id}-manifest`));
		console.log(JSON.stringify(manifest, null, 2));
		console.log(`Manifest: ${output}`);
		return;
	}
	if (command === "issue-preflight") return runIssuePreflightCommand(args);
	if (command === "issue-smoke") return runIssueSmokeCommand(args);
	if (command === "issue-run") return runIssueTaskCommand(args);
	if (command === "issue-nanocursor-validate") {
		const nanoCursorRoot = option(args, "--nanocursor-root");
		if (!nanoCursorRoot) throw new Error("issue-nanocursor-validate requires --nanocursor-root.");
		console.log(JSON.stringify(await validateNanoCursorCandidate(nanoCursorRoot), null, 2));
		return;
	}
	if (command === "issue-nanocursor-run") return runNanoCursorIssueTaskCommand(args);
	throw new Error(`Unknown command: ${command}\n\n${usage()}`);
}

main().catch((error: unknown) => {
	console.error(error instanceof Error ? error.message : String(error));
	process.exitCode = 1;
});
