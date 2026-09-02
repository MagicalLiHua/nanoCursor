import { access, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";

export interface AgentEvalConfig {
	provider: "deepseek" | "opencode-go";
	baseUrl: string;
	apiKey: string;
	model: string;
	outboundProxyUrl?: string;
	envFile: string;
}

export type AgentEvalProvider = AgentEvalConfig["provider"];

function parseEnvFile(content: string): Record<string, string> {
	const values: Record<string, string> = {};
	for (const line of content.split(/\r?\n/)) {
		const trimmed = line.trim();
		if (!trimmed || trimmed.startsWith("#")) continue;
		const separator = trimmed.indexOf("=");
		if (separator < 1) continue;
		const key = trimmed.slice(0, separator).trim();
		let value = trimmed.slice(separator + 1).trim();
		if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
			value = value.slice(1, -1);
		}
		values[key] = value;
	}
	return values;
}

async function findEnvFile(start: string): Promise<string | undefined> {
	let current = resolve(start);
	for (let depth = 0; depth < 7; depth++) {
		const candidate = join(current, ".env");
		try {
			await access(candidate);
			return candidate;
		} catch {
			const parent = dirname(current);
			if (parent === current) break;
			current = parent;
		}
	}
	return undefined;
}

function firstValue(values: Record<string, string>, keys: string[]): string | undefined {
	for (const key of keys) {
		const value = (process.env[key] ?? values[key])?.trim();
		if (value) return value;
	}
	return undefined;
}

function required(values: Record<string, string>, keys: string[]): string {
	const value = firstValue(values, keys);
	if (!value) throw new Error(`Missing required configuration; set one of: ${keys.join(", ")}`);
	return value;
}

export async function loadAgentEvalConfig(envFile?: string): Promise<AgentEvalConfig> {
	return loadAgentEvalProviderConfig(envFile);
}

export function normalizeAgentEvalProvider(value: string): AgentEvalProvider {
	const normalized = value.trim().toLowerCase();
	if (normalized === "deepseek") return "deepseek";
	if (["opencode-go", "opencodego", "open-code-go"].includes(normalized)) return "opencode-go";
	throw new Error(`Unsupported model provider: ${value}`);
}

export function normalizeOpenAIBaseUrl(value: string): string {
	const url = new URL(value);
	url.username = "";
	url.password = "";
	url.search = "";
	url.hash = "";
	url.pathname = url.pathname.replace(/\/chat\/completions\/?$/, "").replace(/\/$/, "");
	return url.toString().replace(/\/$/, "");
}

export async function loadAgentEvalProviderConfig(
	envFile?: string,
	providerOverride?: string,
): Promise<AgentEvalConfig> {
	const resolvedEnvFile = envFile ? resolve(envFile) : await findEnvFile(process.cwd());
	const values = resolvedEnvFile ? parseEnvFile(await readFile(resolvedEnvFile, "utf8")) : {};
	const provider = normalizeAgentEvalProvider(
		providerOverride ?? firstValue(values, ["AGENT_EVAL_PROVIDER", "RAG_LLM_PROVIDER"]) ?? "deepseek",
	);
	const baseUrl =
		provider === "opencode-go"
			? normalizeOpenAIBaseUrl(
					required(values, ["OPENCODE_GO_ENDPOINT", "OPENCODE_GO_BASE_URL", "AGENT_EVAL_BASE_URL"]),
				)
			: required(values, ["AGENT_EVAL_BASE_URL", "RAG_LLM_BASE_URL", "DEEPSEEK_BASE_URL"]);
	new URL(baseUrl);
	const proxy = firstValue(values, ["AGENT_EVAL_OUTBOUND_PROXY_URL", "RAG_OUTBOUND_PROXY_URL"]);
	if (proxy) new URL(proxy);
	return {
		provider,
		baseUrl,
		apiKey:
			provider === "opencode-go"
				? required(values, ["OPENCODE_GO_API_KEY", "OPENCODE_API_KEY", "AGENT_EVAL_API_KEY"])
				: required(values, ["AGENT_EVAL_API_KEY", "RAG_LLM_API_KEY", "DEEPSEEK_API_KEY"]),
		model:
			provider === "opencode-go"
				? required(values, ["OPENCODE_GO_MODEL", "AGENT_EVAL_MODEL"])
				: required(values, ["AGENT_EVAL_MODEL", "RAG_LLM_MODEL", "DEEPSEEK_MODEL"]),
		...(proxy ? { outboundProxyUrl: proxy } : {}),
		envFile: resolvedEnvFile ?? "process-environment",
	};
}

export function redactedConfig(config: AgentEvalConfig): Omit<AgentEvalConfig, "apiKey"> & { apiKey: string } {
	return { ...config, apiKey: "***" };
}
