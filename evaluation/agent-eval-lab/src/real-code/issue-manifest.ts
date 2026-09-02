import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { DEEPSEEK_CONTEXT_WINDOW, DEEPSEEK_MAX_OUTPUT_TOKENS } from "../model/deepseek-config.ts";
import { issueAgentSystemPrompt } from "./issue-system-prompt.ts";
import type { IssueTask } from "./issue-types.ts";

export interface IssueFrozenManifest {
	protocolVersion:
		| "issue-agent-eval-v1.0-dev"
		| "issue-agent-eval-v1.0"
		| "issue-agent-eval-v1.0.1"
		| "issue-agent-eval-v1.0.2"
		| "issue-agent-eval-v1.1-model-comparison";
	taskId: string;
	instanceId: string;
	repository: string;
	baseCommit: string;
	image: string;
	split: string;
	hashes: {
		issue: string;
		prompt: string;
		goldPatch: string;
		testPatch: string;
		commands: string;
		taskDefinition: string;
		systemPrompt: string;
		issueToolsSource: string;
		dockerSandboxSource: string;
		issueRunnerSource: string;
		issueProtocolSource: string;
		modelRuntimeSource: string;
		modelConfigSource: string;
		candidatePackageConfig?: string;
		candidateAgentSource?: string;
		candidateClientSource?: string;
		candidateAdapterSource?: string;
		candidateRunnerSource?: string;
		toolBridgeSource?: string;
	};
	experiment: {
		maxTurns: number;
		maxWallTimeMs: number;
		commandTimeoutMs: number;
		containerCpus: 2;
		containerMemoryMiB: 4096;
		containerPidsLimit: 512;
		containerNetwork: "none";
		toolExecution: "sequential";
		modelProvider: string;
		modelId: string;
		modelApi: string;
		modelBaseUrl: string;
		modelContextWindow: number;
		modelMaxOutputTokens: number;
	};
}

export interface IssueManifestModel {
	provider: string;
	id: string;
	api: string;
	baseUrl: string;
	contextWindow: number;
	maxTokens: number;
}

function sha256(value: string | Buffer): string {
	return createHash("sha256").update(value).digest("hex");
}

async function sourceHash(name: string): Promise<string> {
	return sha256(await readFile(new URL(`../../src/real-code/${name}`, import.meta.url)));
}

export async function buildIssueFrozenManifest(
	task: IssueTask,
	limits: { maxTurns: number; maxWallTimeMs: number; commandTimeoutMs: number },
	model?: IssueManifestModel,
): Promise<IssueFrozenManifest> {
	const frozenModel = model ?? {
		provider: "deepseek",
		id: "deepseek-v4-flash",
		api: "openai-completions",
		baseUrl: "https://api.deepseek.com",
		contextWindow: DEEPSEEK_CONTEXT_WINDOW,
		maxTokens: DEEPSEEK_MAX_OUTPUT_TOKENS,
	};
	const commands = JSON.stringify({
		target: task.hiddenTestCommand,
		regression: task.regressionCommand,
		newTestCommandPrefix: task.newTestCommandPrefix,
		newTestPathMode: task.newTestPathMode,
		testPathMode: task.testPathMode,
	});
	const taskDefinition = JSON.stringify({
		id: task.id,
		title: task.title,
		instanceId: task.instanceId,
		repository: task.repository,
		baseCommit: task.baseCommit,
		image: task.image,
		difficulty: task.difficulty,
		split: task.split,
		issue: task.issue,
		prompt: task.prompt,
		commands,
	});
	return {
		protocolVersion: model
			? "issue-agent-eval-v1.1-model-comparison"
			: task.split === "development"
				? "issue-agent-eval-v1.0-dev"
				: "issue-agent-eval-v1.0.2",
		taskId: task.id,
		instanceId: task.instanceId,
		repository: task.repository,
		baseCommit: task.baseCommit,
		image: task.image,
		split: task.split,
		hashes: {
			issue: sha256(task.issue),
			prompt: sha256(task.prompt),
			goldPatch: sha256(await readFile(task.goldPatchPath)),
			testPatch: sha256(await readFile(task.upstreamTestPatchPath)),
			commands: sha256(commands),
			taskDefinition: sha256(taskDefinition),
			systemPrompt: sha256(issueAgentSystemPrompt()),
			issueToolsSource: await sourceHash("issue-tools.ts"),
			dockerSandboxSource: await sourceHash("docker-sandbox.ts"),
			issueRunnerSource: await sourceHash("issue-runner.ts"),
			issueProtocolSource: await sourceHash("issue-protocol.ts"),
			modelRuntimeSource: sha256(await readFile(new URL("../../src/model/deepseek.ts", import.meta.url))),
			modelConfigSource: sha256(await readFile(new URL("../../src/config.ts", import.meta.url))),
		},
		experiment: {
			maxTurns: limits.maxTurns,
			maxWallTimeMs: limits.maxWallTimeMs,
			commandTimeoutMs: limits.commandTimeoutMs,
			containerCpus: 2,
			containerMemoryMiB: 4096,
			containerPidsLimit: 512,
			containerNetwork: "none",
			toolExecution: "sequential",
			modelProvider: frozenModel.provider,
			modelId: frozenModel.id,
			modelApi: frozenModel.api,
			modelBaseUrl: frozenModel.baseUrl,
			modelContextWindow: frozenModel.contextWindow,
			modelMaxOutputTokens: frozenModel.maxTokens,
		},
	};
}
