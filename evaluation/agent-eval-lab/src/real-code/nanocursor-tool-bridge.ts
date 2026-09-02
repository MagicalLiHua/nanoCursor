import { randomBytes, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";

const PROTOCOL_VERSION = "1";
const DEFAULT_MAX_BODY_BYTES = 1_000_000;

export const NANO_CURSOR_TOOL_NAMES = [
	"repo_list",
	"repo_read",
	"repo_search",
	"repo_write",
	"repo_replace",
	"repo_delete",
	"repo_diff",
	"command_run",
] as const;

export type NanoCursorToolName = (typeof NANO_CURSOR_TOOL_NAMES)[number];

export interface NanoCursorToolSandbox {
	list(path: string, depth: number): Promise<unknown>;
	read(path: string, startLine: number, maxLines: number): Promise<unknown>;
	search(path: string, query: string): Promise<unknown>;
	writeRepositoryFile(path: string, content: string): Promise<unknown>;
	replaceRepositoryText(path: string, oldText: string, newText: string, expectedOccurrences: number): Promise<unknown>;
	deleteRepositoryFile(path: string): Promise<unknown>;
	repositoryDiff(): Promise<unknown>;
	runRepositoryCommand(argv: string[], timeoutMs?: number): Promise<unknown>;
}

export interface NanoCursorToolBridgeOptions {
	host?: "127.0.0.1" | "::1";
	port?: number;
	token?: string;
	maxBodyBytes?: number;
}

export interface NanoCursorToolBridgeHandle {
	url: string;
	token: string;
	close(): Promise<void>;
}

interface ToolCallRequest {
	protocolVersion: string;
	toolCallId: string;
	tool: NanoCursorToolName;
	arguments: Record<string, unknown>;
}

class HttpError extends Error {
	readonly statusCode: number;

	constructor(statusCode: number, message: string) {
		super(message);
		this.statusCode = statusCode;
	}
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireOnlyKeys(arguments_: Record<string, unknown>, allowed: readonly string[]): void {
	const unexpected = Object.keys(arguments_).filter((key) => !allowed.includes(key));
	if (unexpected.length > 0) throw new HttpError(400, `Unexpected argument(s): ${unexpected.join(", ")}.`);
}

function requiredString(arguments_: Record<string, unknown>, key: string, maxLength: number): string {
	const value = arguments_[key];
	if (typeof value !== "string" || value.length === 0) throw new HttpError(400, `${key} must be a non-empty string.`);
	if (value.length > maxLength) throw new HttpError(400, `${key} must not exceed ${maxLength} characters.`);
	return value;
}

function optionalString(arguments_: Record<string, unknown>, key: string, fallback: string, maxLength: number): string {
	const value = arguments_[key];
	if (value === undefined) return fallback;
	if (typeof value !== "string") throw new HttpError(400, `${key} must be a string.`);
	if (value.length > maxLength) throw new HttpError(400, `${key} must not exceed ${maxLength} characters.`);
	return value;
}

function optionalInteger(
	arguments_: Record<string, unknown>,
	key: string,
	fallback: number,
	minimum: number,
	maximum: number,
): number {
	const value = arguments_[key];
	if (value === undefined) return fallback;
	if (!Number.isInteger(value) || typeof value !== "number" || value < minimum || value > maximum) {
		throw new HttpError(400, `${key} must be an integer from ${minimum} to ${maximum}.`);
	}
	return value;
}

function parseToolCall(value: unknown): ToolCallRequest {
	if (!isRecord(value)) throw new HttpError(400, "Request body must be a JSON object.");
	requireOnlyKeys(value, ["protocolVersion", "toolCallId", "tool", "arguments"]);
	if (value.protocolVersion !== PROTOCOL_VERSION) throw new HttpError(400, "Unsupported protocolVersion.");
	if (typeof value.toolCallId !== "string" || value.toolCallId.length === 0 || value.toolCallId.length > 200) {
		throw new HttpError(400, "toolCallId must be a non-empty string of at most 200 characters.");
	}
	if (typeof value.tool !== "string" || !NANO_CURSOR_TOOL_NAMES.includes(value.tool as NanoCursorToolName)) {
		throw new HttpError(400, "Unknown tool.");
	}
	if (!isRecord(value.arguments)) throw new HttpError(400, "arguments must be a JSON object.");
	return {
		protocolVersion: PROTOCOL_VERSION,
		toolCallId: value.toolCallId,
		tool: value.tool as NanoCursorToolName,
		arguments: value.arguments,
	};
}

async function dispatchTool(sandbox: NanoCursorToolSandbox, request: ToolCallRequest): Promise<unknown> {
	const arguments_ = request.arguments;
	switch (request.tool) {
		case "repo_list": {
			requireOnlyKeys(arguments_, ["path", "depth"]);
			return sandbox.list(
				optionalString(arguments_, "path", ".", 2_000),
				optionalInteger(arguments_, "depth", 2, 1, 4),
			);
		}
		case "repo_read": {
			requireOnlyKeys(arguments_, ["path", "start_line", "max_lines"]);
			return sandbox.read(
				requiredString(arguments_, "path", 2_000),
				optionalInteger(arguments_, "start_line", 1, 1, Number.MAX_SAFE_INTEGER),
				optionalInteger(arguments_, "max_lines", 160, 1, 240),
			);
		}
		case "repo_search": {
			requireOnlyKeys(arguments_, ["query", "path"]);
			return sandbox.search(
				optionalString(arguments_, "path", ".", 2_000),
				requiredString(arguments_, "query", 200),
			);
		}
		case "repo_write": {
			requireOnlyKeys(arguments_, ["path", "content"]);
			return sandbox.writeRepositoryFile(
				requiredString(arguments_, "path", 2_000),
				requiredString(arguments_, "content", 120_000),
			);
		}
		case "repo_replace": {
			requireOnlyKeys(arguments_, ["path", "old_text", "new_text", "expected_occurrences"]);
			const newText = arguments_.new_text;
			if (typeof newText !== "string" || newText.length > 60_000) {
				throw new HttpError(400, "new_text must be a string of at most 60000 characters.");
			}
			return sandbox.replaceRepositoryText(
				requiredString(arguments_, "path", 2_000),
				requiredString(arguments_, "old_text", 60_000),
				newText,
				optionalInteger(arguments_, "expected_occurrences", 1, 1, 20),
			);
		}
		case "repo_delete": {
			requireOnlyKeys(arguments_, ["path"]);
			return sandbox.deleteRepositoryFile(requiredString(arguments_, "path", 2_000));
		}
		case "repo_diff": {
			requireOnlyKeys(arguments_, []);
			return sandbox.repositoryDiff();
		}
		case "command_run": {
			requireOnlyKeys(arguments_, ["argv", "timeout_ms"]);
			const argv = arguments_.argv;
			if (!Array.isArray(argv) || argv.length < 1 || argv.length > 80) {
				throw new HttpError(400, "argv must contain from 1 to 80 strings.");
			}
			if (
				!argv.every((argument) => typeof argument === "string" && argument.length >= 1 && argument.length <= 2_000)
			) {
				throw new HttpError(400, "Each argv entry must be a non-empty string of at most 2000 characters.");
			}
			const timeoutMs =
				arguments_.timeout_ms === undefined
					? undefined
					: optionalInteger(arguments_, "timeout_ms", 180_000, 10_000, 180_000);
			return sandbox.runRepositoryCommand(argv, timeoutMs);
		}
	}
}

function authorized(header: string | undefined, token: string): boolean {
	if (!header?.startsWith("Bearer ")) return false;
	const supplied = Buffer.from(header.slice("Bearer ".length));
	const expected = Buffer.from(token);
	return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

async function readJsonBody(request: IncomingMessage, maxBodyBytes: number): Promise<unknown> {
	const chunks: Buffer[] = [];
	let bytes = 0;
	for await (const chunk of request) {
		const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
		bytes += buffer.length;
		if (bytes > maxBodyBytes) throw new HttpError(413, "Request body is too large.");
		chunks.push(buffer);
	}
	try {
		return JSON.parse(Buffer.concat(chunks).toString("utf8"));
	} catch {
		throw new HttpError(400, "Request body must be valid JSON.");
	}
}

function respond(response: ServerResponse, statusCode: number, body: Record<string, unknown>): void {
	response.writeHead(statusCode, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
	response.end(JSON.stringify(body));
}

function closeServer(server: Server): Promise<void> {
	return new Promise((resolve, reject) => {
		server.close((error) => {
			if (error) reject(error);
			else resolve();
		});
	});
}

export async function startNanoCursorToolBridge(
	sandbox: NanoCursorToolSandbox,
	options: NanoCursorToolBridgeOptions = {},
): Promise<NanoCursorToolBridgeHandle> {
	const host = options.host ?? "127.0.0.1";
	const port = options.port ?? 0;
	const token = options.token ?? randomBytes(32).toString("hex");
	const maxBodyBytes = options.maxBodyBytes ?? DEFAULT_MAX_BODY_BYTES;
	if (!token) throw new Error("Tool bridge token must not be empty.");
	if (!Number.isInteger(port) || port < 0 || port > 65_535) throw new Error("Tool bridge port is invalid.");
	if (!Number.isInteger(maxBodyBytes) || maxBodyBytes < 1) throw new Error("maxBodyBytes must be positive.");
	let queue: Promise<void> = Promise.resolve();

	const server = createServer((request, response) => {
		void (async () => {
			try {
				if (!authorized(request.headers.authorization, token)) throw new HttpError(401, "Unauthorized.");
				if (request.method === "GET" && request.url === "/health") {
					respond(response, 200, { ok: true, protocolVersion: PROTOCOL_VERSION });
					return;
				}
				if (request.method !== "POST" || request.url !== "/v1/tool-call") {
					throw new HttpError(404, "Not found.");
				}
				if (!request.headers["content-type"]?.toLowerCase().startsWith("application/json")) {
					throw new HttpError(415, "Content-Type must be application/json.");
				}
				const call = parseToolCall(await readJsonBody(request, maxBodyBytes));
				const startedAt = Date.now();
				const execution = queue.then(() => dispatchTool(sandbox, call));
				queue = execution.then(
					() => undefined,
					() => undefined,
				);
				const result = await execution;
				respond(response, 200, {
					ok: true,
					protocolVersion: PROTOCOL_VERSION,
					toolCallId: call.toolCallId,
					tool: call.tool,
					durationMs: Date.now() - startedAt,
					result,
				});
			} catch (error) {
				const statusCode = error instanceof HttpError ? error.statusCode : 422;
				respond(response, statusCode, {
					ok: false,
					protocolVersion: PROTOCOL_VERSION,
					error: error instanceof Error ? error.message : String(error),
				});
			}
		})();
	});

	await new Promise<void>((resolve, reject) => {
		const onError = (error: Error): void => {
			server.off("listening", onListening);
			reject(error);
		};
		const onListening = (): void => {
			server.off("error", onError);
			resolve();
		};
		server.once("error", onError);
		server.once("listening", onListening);
		server.listen(port, host);
	});
	const address = server.address();
	if (!address || typeof address === "string") {
		await closeServer(server);
		throw new Error("Tool bridge did not expose a TCP address.");
	}
	const urlHost = host === "::1" ? "[::1]" : host;
	return {
		url: `http://${urlHost}:${address.port}`,
		token,
		close: () => closeServer(server),
	};
}
