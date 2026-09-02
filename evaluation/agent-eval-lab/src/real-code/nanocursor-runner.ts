import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { AgentEvalConfig } from "../config.ts";
import { toJsonValue } from "../json.ts";
import { DEEPSEEK_CONTEXT_WINDOW, DEEPSEEK_MAX_OUTPUT_TOKENS } from "../model/deepseek-config.ts";
import type { TraceEvent } from "../types.ts";
import { DockerRealCodeSandbox } from "./docker-sandbox.ts";
import { buildIssueFrozenManifest } from "./issue-manifest.ts";
import { isProviderInfrastructureError } from "./issue-protocol.ts";
import { issueAgentSystemPrompt } from "./issue-system-prompt.ts";
import type { IssueEvalResult, IssueOutcomeStatus, IssueRunOptions, IssueTask } from "./issue-types.ts";
import { type NanoCursorToolBridgeHandle, startNanoCursorToolBridge } from "./nanocursor-tool-bridge.ts";
import type { ProcessResult, RealCodeTerminationReason } from "./types.ts";

const MAX_CAPTURE_BYTES = 128 * 1024;

export interface NanoCursorIssueRunOptions extends IssueRunOptions {
	nanoCursorRoot: string;
	outputDirectory?: string;
	uvExecutable?: string;
}

interface NanoCursorSummary {
	run_id: string;
	task_id: string;
	status: string;
	started_at: string;
	finished_at: string;
	model: string;
	max_turns: number;
	max_wall_time_seconds: number;
	turns_used: number;
	input_tokens: number;
	output_tokens: number;
	tool_calls: number;
	tool_errors: number;
	final_response: string;
	errors: string[];
}

interface NanoCursorTraceLine {
	timestamp: string;
	type: string;
	payload: unknown;
}

function appendTail(current: string, chunk: Buffer): string {
	const combined = current + chunk.toString("utf8");
	return combined.length <= MAX_CAPTURE_BYTES ? combined : combined.slice(-MAX_CAPTURE_BYTES);
}

function runProcess(
	program: string,
	args: string[],
	options: { cwd: string; env: NodeJS.ProcessEnv; timeoutMs: number },
): Promise<ProcessResult> {
	const startedAt = Date.now();
	return new Promise((resolve, reject) => {
		const child = spawn(program, args, { cwd: options.cwd, env: options.env, stdio: ["ignore", "pipe", "pipe"] });
		let stdout = "";
		let stderr = "";
		let timedOut = false;
		const timer = setTimeout(() => {
			timedOut = true;
			child.kill("SIGKILL");
		}, options.timeoutMs);
		child.stdout.on("data", (chunk: Buffer) => {
			stdout = appendTail(stdout, chunk);
		});
		child.stderr.on("data", (chunk: Buffer) => {
			stderr = appendTail(stderr, chunk);
		});
		child.on("error", (error) => {
			clearTimeout(timer);
			reject(error);
		});
		child.on("close", (code) => {
			clearTimeout(timer);
			resolve({
				exitCode: code ?? (timedOut ? 124 : 1),
				stdout,
				stderr,
				durationMs: Date.now() - startedAt,
				timedOut,
			});
		});
	});
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: Record<string, unknown>, key: string): string {
	const field = value[key];
	if (typeof field !== "string") throw new Error(`NanoCursor summary field ${key} is invalid.`);
	return field;
}

function requiredNumber(value: Record<string, unknown>, key: string): number {
	const field = value[key];
	if (typeof field !== "number" || !Number.isFinite(field)) {
		throw new Error(`NanoCursor summary field ${key} is invalid.`);
	}
	return field;
}

function parseSummary(value: unknown): NanoCursorSummary {
	if (!isRecord(value)) throw new Error("NanoCursor summary must be a JSON object.");
	const errors = value.errors;
	if (!Array.isArray(errors) || !errors.every((entry) => typeof entry === "string")) {
		throw new Error("NanoCursor summary field errors is invalid.");
	}
	return {
		run_id: requiredString(value, "run_id"),
		task_id: requiredString(value, "task_id"),
		status: requiredString(value, "status"),
		started_at: requiredString(value, "started_at"),
		finished_at: requiredString(value, "finished_at"),
		model: requiredString(value, "model"),
		max_turns: requiredNumber(value, "max_turns"),
		max_wall_time_seconds: requiredNumber(value, "max_wall_time_seconds"),
		turns_used: requiredNumber(value, "turns_used"),
		input_tokens: requiredNumber(value, "input_tokens"),
		output_tokens: requiredNumber(value, "output_tokens"),
		tool_calls: requiredNumber(value, "tool_calls"),
		tool_errors: requiredNumber(value, "tool_errors"),
		final_response: requiredString(value, "final_response"),
		errors,
	};
}

async function readSummary(path: string): Promise<NanoCursorSummary> {
	return parseSummary(JSON.parse(await readFile(path, "utf8")));
}

async function readTrace(path: string): Promise<TraceEvent[]> {
	const content = await readFile(path, "utf8");
	return content
		.split(/\r?\n/)
		.filter((line) => line.trim().length > 0)
		.map((line, index): TraceEvent => {
			const parsed = JSON.parse(line) as NanoCursorTraceLine;
			if (typeof parsed.timestamp !== "string" || typeof parsed.type !== "string") {
				throw new Error(`NanoCursor trace line ${index + 1} is invalid.`);
			}
			return {
				sequence: index + 1,
				timestamp: parsed.timestamp,
				type: parsed.type,
				payload: toJsonValue(parsed.payload),
			};
		});
}

function terminationReason(summary: NanoCursorSummary, process: ProcessResult): RealCodeTerminationReason {
	if (summary.status === "timeout" || process.timedOut) return "wall-time-limit";
	if (summary.turns_used >= summary.max_turns && summary.errors.some((error) => /maximum iterations/i.test(error))) {
		return "turn-limit";
	}
	if (summary.status !== "completed" || process.exitCode !== 0) return "runtime-error";
	return "completed";
}

function outcomeStatus(
	gradePassed: boolean,
	forbiddenChanges: string[],
	hiddenTestsInjected: boolean,
	finalResponse: string,
	termination: RealCodeTerminationReason,
): IssueOutcomeStatus {
	if (termination !== "completed") return "INFRA_BLOCKED";
	if (forbiddenChanges.length > 0) return "INVALID";
	if (!hiddenTestsInjected) return "INFRA_BLOCKED";
	if (gradePassed && finalResponse.trim()) return "COMPLETED";
	return "PARTIAL";
}

function sha256(value: string | Buffer): string {
	return createHash("sha256").update(value).digest("hex");
}

export interface NanoCursorCandidateValidation {
	nanoCursorRoot: string;
	systemPromptSha256: string;
	hashes: {
		candidatePackageConfig: string;
		candidateAgentSource: string;
		candidateClientSource: string;
		candidateAdapterSource: string;
		candidateRunnerSource: string;
		toolBridgeSource: string;
	};
}

async function candidateHashes(nanoCursorRoot: string): Promise<{
	candidatePackageConfig: string;
	candidateAgentSource: string;
	candidateClientSource: string;
	candidateAdapterSource: string;
	candidateRunnerSource: string;
	toolBridgeSource: string;
}> {
	const adapterFiles = [
		"__init__.py",
		"__main__.py",
		"bridge_client.py",
		"contract.py",
		"issue_agent_system_prompt.txt",
		"runner.py",
		"tools.py",
		"trace.py",
	];
	const adapterContents = await Promise.all(
		adapterFiles.map(
			async (path) => `${path}\0${await readFile(join(nanoCursorRoot, "nanocursor", "eval", path), "utf8")}`,
		),
	);
	return {
		candidatePackageConfig: sha256(await readFile(join(nanoCursorRoot, "pyproject.toml"))),
		candidateAgentSource: sha256(await readFile(join(nanoCursorRoot, "nanocursor", "agent.py"))),
		candidateClientSource: sha256(await readFile(join(nanoCursorRoot, "nanocursor", "client.py"))),
		candidateAdapterSource: sha256(adapterContents.join("\0")),
		candidateRunnerSource: sha256(
			await readFile(new URL("../../src/real-code/nanocursor-runner.ts", import.meta.url)),
		),
		toolBridgeSource: sha256(
			await readFile(new URL("../../src/real-code/nanocursor-tool-bridge.ts", import.meta.url)),
		),
	};
}

export async function validateNanoCursorCandidate(nanoCursorRootInput: string): Promise<NanoCursorCandidateValidation> {
	const nanoCursorRoot = resolve(nanoCursorRootInput);
	const candidateSystemPrompt = (
		await readFile(join(nanoCursorRoot, "nanocursor", "eval", "issue_agent_system_prompt.txt"), "utf8")
	).replace(/\r?\n$/, "");
	if (candidateSystemPrompt !== issueAgentSystemPrompt()) {
		throw new Error("NanoCursor and Pi system prompts differ; refusing to run a confounded comparison.");
	}
	return {
		nanoCursorRoot,
		systemPromptSha256: sha256(candidateSystemPrompt),
		hashes: await candidateHashes(nanoCursorRoot),
	};
}

export async function runNanoCursorIssueTask(
	task: IssueTask,
	config: AgentEvalConfig,
	options: NanoCursorIssueRunOptions,
): Promise<IssueEvalResult> {
	const trialIndex = options.trialIndex ?? 1;
	const attemptIndex = options.attemptIndex ?? 1;
	const maxTurns = options.maxTurns ?? 96;
	const maxWallTimeMs = options.maxWallTimeMs ?? 20 * 60_000;
	const commandTimeoutMs = options.commandTimeoutMs ?? 180_000;
	const runId = options.runId ?? `${task.id}-nanocursor-t${trialIndex}-a${attemptIndex}-${randomUUID().slice(0, 8)}`;
	const nanoCursorRoot = resolve(options.nanoCursorRoot);
	const candidateValidation = await validateNanoCursorCandidate(nanoCursorRoot);
	const outputDirectory = resolve(options.outputDirectory ?? join(nanoCursorRoot, ".artifacts", "nanocursor-eval"));
	await mkdir(outputDirectory, { recursive: true });
	const summaryPath = join(outputDirectory, `${runId}.summary.json`);
	const tracePath = join(outputDirectory, `${runId}.trace.jsonl`);
	const sandbox = new DockerRealCodeSandbox(task, { ...options, runId });
	await sandbox.start();
	let bridge: NanoCursorToolBridgeHandle;
	try {
		bridge = await startNanoCursorToolBridge(sandbox);
	} catch (error) {
		await sandbox.close();
		throw error;
	}
	let processResult: ProcessResult | undefined;
	let runError: string | undefined;
	try {
		const env: NodeJS.ProcessEnv = {
			...process.env,
			OPENAI_API_KEY: config.apiKey,
			NANOCURSOR_TOOL_BRIDGE_URL: bridge.url,
			NANOCURSOR_TOOL_BRIDGE_TOKEN: bridge.token,
		};
		const noProxy = [process.env.NO_PROXY, process.env.no_proxy, "127.0.0.1", "localhost", "::1"]
			.filter((value) => value)
			.join(",");
		env.NO_PROXY = noProxy;
		env.no_proxy = noProxy;
		if (config.outboundProxyUrl) {
			env.HTTP_PROXY = config.outboundProxyUrl;
			env.HTTPS_PROXY = config.outboundProxyUrl;
			env.http_proxy = config.outboundProxyUrl;
			env.https_proxy = config.outboundProxyUrl;
		}
		processResult = await runProcess(
			options.uvExecutable ?? "uv",
			[
				"run",
				"--frozen",
				"python",
				"-m",
				"nanocursor.eval",
				"--prompt",
				task.prompt,
				"--task-id",
				task.id,
				"--run-id",
				runId,
				"--base-url",
				config.baseUrl,
				"--model",
				config.model,
				"--context-window",
				String(DEEPSEEK_CONTEXT_WINDOW),
				"--max-output-tokens",
				String(DEEPSEEK_MAX_OUTPUT_TOKENS),
				"--output-dir",
				outputDirectory,
				"--max-turns",
				String(maxTurns),
				"--max-wall-seconds",
				String(Math.ceil(maxWallTimeMs / 1_000)),
			],
			{ cwd: nanoCursorRoot, env, timeoutMs: maxWallTimeMs + 60_000 },
		);
		if (processResult.exitCode !== 0) {
			runError = processResult.stderr.trim() || `NanoCursor exited ${processResult.exitCode}.`;
		}
	} catch (error) {
		runError = error instanceof Error ? error.message : String(error);
	} finally {
		await bridge.close();
	}

	try {
		const summary = await readSummary(summaryPath);
		const trace = await readTrace(tracePath);
		const process = processResult ?? {
			exitCode: 1,
			stdout: "",
			stderr: runError ?? "NanoCursor did not start.",
			durationMs: 0,
			timedOut: false,
		};
		const termination = terminationReason(summary, process);
		const grade = await sandbox.gradeIssue();
		const status = outcomeStatus(
			grade.passed,
			grade.forbiddenChanges,
			grade.checks.find((check) => check.id === "hidden-tests-injected")?.passed ?? false,
			summary.final_response,
			termination,
		);
		const model = {
			provider: config.provider,
			id: config.model,
			api: "openai-completions",
			baseUrl: config.baseUrl,
			contextWindow: DEEPSEEK_CONTEXT_WINDOW,
			maxTokens: DEEPSEEK_MAX_OUTPUT_TOKENS,
		};
		const baseManifest = await buildIssueFrozenManifest(task, { maxTurns, maxWallTimeMs, commandTimeoutMs }, model);
		const manifest = {
			...baseManifest,
			hashes: { ...baseManifest.hashes, ...candidateValidation.hashes },
		};
		const effectiveModelAction = summary.tool_calls > 0 || summary.final_response.trim().length > 0;
		const effectiveError = (runError ?? summary.errors.join("\n")) || undefined;
		return {
			runId,
			taskId: task.id,
			instanceId: task.instanceId,
			trialIndex,
			attemptIndex,
			runtime: "nanocursor-n0",
			model: summary.model,
			startedAt: summary.started_at,
			finishedAt: summary.finished_at,
			passed: status === "COMPLETED",
			outcomeStatus: status,
			terminationReason: termination,
			providerRetryEligible:
				!effectiveModelAction && effectiveError !== undefined && isProviderInfrastructureError(effectiveError),
			manifest,
			budget: { maxTurns, maxWallTimeMs, turnsUsed: summary.turns_used },
			finalResponse: summary.final_response,
			trace,
			grade,
			...(effectiveError ? { runError: effectiveError } : {}),
		};
	} finally {
		await sandbox.close();
	}
}
