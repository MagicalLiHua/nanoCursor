import type { Model, Provider, ProviderEnv, StreamFunction } from "@earendil-works/pi-ai";
import { deepseekProvider } from "@earendil-works/pi-ai/providers/deepseek";
import { opencodeGoProvider } from "@earendil-works/pi-ai/providers/opencode-go";
import { OPENCODE_GO_MODELS } from "@earendil-works/pi-ai/providers/opencode-go.models";
import type { AgentEvalConfig } from "../config.ts";
import { DEEPSEEK_CONTEXT_WINDOW, DEEPSEEK_MAX_OUTPUT_TOKENS } from "./deepseek-config.ts";

export interface AgentEvalRuntime {
	model: Model<"openai-completions">;
	streamFn: StreamFunction;
	getApiKey: () => string;
}

export type DeepSeekRuntime = AgentEvalRuntime;

function proxyEnv(config: AgentEvalConfig): ProviderEnv | undefined {
	return config.outboundProxyUrl
		? {
				http_proxy: config.outboundProxyUrl,
				https_proxy: config.outboundProxyUrl,
				HTTP_PROXY: config.outboundProxyUrl,
				HTTPS_PROXY: config.outboundProxyUrl,
			}
		: undefined;
}

function runtime(
	config: AgentEvalConfig,
	model: Model<"openai-completions">,
	provider: Provider<"openai-completions">,
): AgentEvalRuntime {
	const outboundProxy = proxyEnv(config);
	const streamFn: StreamFunction = (_requestModel, context, options) =>
		provider.streamSimple(model, context, {
			...options,
			...(outboundProxy ? { env: { ...options?.env, ...outboundProxy } } : {}),
		});
	return { model, streamFn, getApiKey: () => config.apiKey };
}

function createDeepSeek(config: AgentEvalConfig): AgentEvalRuntime {
	const model: Model<"openai-completions"> = {
		id: config.model,
		name: config.model,
		api: "openai-completions",
		provider: "deepseek",
		baseUrl: config.baseUrl,
		reasoning: false,
		input: ["text"],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: DEEPSEEK_CONTEXT_WINDOW,
		maxTokens: DEEPSEEK_MAX_OUTPUT_TOKENS,
	};
	const upstream = deepseekProvider();
	const provider: Provider<"openai-completions"> = {
		...upstream,
		getModels: () => [model],
	};
	return runtime(config, model, provider);
}

function createOpenCodeGo(config: AgentEvalConfig): AgentEvalRuntime {
	const catalogModel = Object.values(OPENCODE_GO_MODELS).find((candidate) => candidate.id === config.model);
	if (!catalogModel) throw new Error(`Unknown opencode-go model: ${config.model}`);
	if (catalogModel.api !== "openai-completions") {
		throw new Error(`opencode-go model ${config.model} uses unsupported API ${catalogModel.api}.`);
	}
	const model: Model<"openai-completions"> = {
		...(catalogModel as Model<"openai-completions">),
		baseUrl: config.baseUrl,
		maxTokens: Math.min(catalogModel.maxTokens, DEEPSEEK_MAX_OUTPUT_TOKENS),
	};
	return runtime(config, model, opencodeGoProvider() as Provider<"openai-completions">);
}

export function createAgentEvalRuntime(config: AgentEvalConfig): AgentEvalRuntime {
	return config.provider === "opencode-go" ? createOpenCodeGo(config) : createDeepSeek(config);
}

export function createDeepSeekRuntime(config: AgentEvalConfig): DeepSeekRuntime {
	if (config.provider !== "deepseek") {
		throw new Error(`createDeepSeekRuntime requires provider=deepseek, received ${config.provider}.`);
	}
	return createDeepSeek(config);
}
