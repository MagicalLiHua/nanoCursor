import { describe, expect, it } from "vitest";
import {
	NANO_CURSOR_TOOL_NAMES,
	type NanoCursorToolSandbox,
	startNanoCursorToolBridge,
} from "../src/real-code/nanocursor-tool-bridge.ts";

class FakeSandbox implements NanoCursorToolSandbox {
	readonly calls: string[] = [];
	active = 0;
	maxActive = 0;

	async list(path: string, depth: number): Promise<unknown> {
		this.calls.push(`list:${path}:${depth}`);
		return "README.md";
	}

	async read(path: string, startLine: number, maxLines: number): Promise<unknown> {
		this.calls.push(`read:${path}:${startLine}:${maxLines}`);
		return "     1\tcontent";
	}

	async search(path: string, query: string): Promise<unknown> {
		this.calls.push(`search:${path}:${query}`);
		return "No matches.";
	}

	async writeRepositoryFile(path: string, content: string): Promise<unknown> {
		this.calls.push(`write:${path}:${content}`);
		return { path };
	}

	async replaceRepositoryText(
		path: string,
		oldText: string,
		newText: string,
		expectedOccurrences: number,
	): Promise<unknown> {
		this.calls.push(`replace:${path}:${oldText}:${newText}:${expectedOccurrences}`);
		return { path, replacements: expectedOccurrences };
	}

	async deleteRepositoryFile(path: string): Promise<unknown> {
		this.calls.push(`delete:${path}`);
		return { path, deleted: true };
	}

	async repositoryDiff(): Promise<unknown> {
		this.calls.push("diff");
		return "STATUS\n(clean)";
	}

	async runRepositoryCommand(argv: string[], timeoutMs?: number): Promise<unknown> {
		this.active += 1;
		this.maxActive = Math.max(this.maxActive, this.active);
		await new Promise((resolve) => setTimeout(resolve, 15));
		this.active -= 1;
		this.calls.push(`command:${argv.join(" ")}:${timeoutMs ?? "default"}`);
		return { exitCode: 0 };
	}
}

async function call(url: string, token: string, tool: string, arguments_: Record<string, unknown>): Promise<Response> {
	return fetch(`${url}/v1/tool-call`, {
		method: "POST",
		headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
		body: JSON.stringify({ protocolVersion: "1", toolCallId: "call-1", tool, arguments: arguments_ }),
	});
}

describe("NanoCursor tool bridge", () => {
	it("exposes exactly the eight issue tools and applies defaults", async () => {
		expect(NANO_CURSOR_TOOL_NAMES).toEqual([
			"repo_list",
			"repo_read",
			"repo_search",
			"repo_write",
			"repo_replace",
			"repo_delete",
			"repo_diff",
			"command_run",
		]);
		const sandbox = new FakeSandbox();
		const bridge = await startNanoCursorToolBridge(sandbox, { token: "test-token" });
		try {
			const response = await call(bridge.url, bridge.token, "repo_list", {});
			expect(response.status).toBe(200);
			expect(await response.json()).toMatchObject({
				ok: true,
				protocolVersion: "1",
				toolCallId: "call-1",
				tool: "repo_list",
				result: "README.md",
			});
			expect(sandbox.calls).toEqual(["list:.:2"]);
		} finally {
			await bridge.close();
		}
	});

	it("rejects missing authentication and malformed tool arguments", async () => {
		const sandbox = new FakeSandbox();
		const bridge = await startNanoCursorToolBridge(sandbox, { token: "test-token" });
		try {
			const unauthorized = await fetch(`${bridge.url}/health`);
			expect(unauthorized.status).toBe(401);
			const malformed = await call(bridge.url, bridge.token, "repo_diff", { path: "." });
			expect(malformed.status).toBe(400);
			expect(await malformed.json()).toMatchObject({ ok: false, error: "Unexpected argument(s): path." });
			expect(sandbox.calls).toEqual([]);
		} finally {
			await bridge.close();
		}
	});

	it("serializes concurrent requests to match the reference harness", async () => {
		const sandbox = new FakeSandbox();
		const bridge = await startNanoCursorToolBridge(sandbox, { token: "test-token" });
		try {
			const [first, second] = await Promise.all([
				call(bridge.url, bridge.token, "command_run", { argv: ["pytest", "-q"] }),
				call(bridge.url, bridge.token, "command_run", { argv: ["pytest", "tests"] }),
			]);
			expect(first.status).toBe(200);
			expect(second.status).toBe(200);
			expect(sandbox.maxActive).toBe(1);
		} finally {
			await bridge.close();
		}
	});
});
