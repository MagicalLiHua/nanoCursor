import type { AgentTool, AgentToolResult } from "@earendil-works/pi-agent-core";
import { Type } from "@earendil-works/pi-ai";
import { toJsonValue } from "../json.ts";
import type { JsonValue } from "../types.ts";
import type { DockerRealCodeSandbox } from "./docker-sandbox.ts";

const RepositoryListParameters = Type.Object({
	path: Type.Optional(Type.String({ default: "." })),
	depth: Type.Optional(Type.Integer({ minimum: 1, maximum: 4, default: 2 })),
});

const RepositoryReadParameters = Type.Object({
	path: Type.String({ minLength: 1 }),
	start_line: Type.Optional(Type.Integer({ minimum: 1, default: 1 })),
	max_lines: Type.Optional(Type.Integer({ minimum: 1, maximum: 240, default: 160 })),
});

const RepositorySearchParameters = Type.Object({
	query: Type.String({ minLength: 1, maxLength: 200 }),
	path: Type.Optional(Type.String({ default: "." })),
});

const TestWriteParameters = Type.Object({
	path: Type.String({ minLength: 1 }),
	content: Type.String({ minLength: 1, maxLength: 60_000 }),
});

const TestRunParameters = Type.Object({});

function textResult(value: unknown): AgentToolResult<JsonValue> {
	const details = toJsonValue(value);
	return { content: [{ type: "text", text: JSON.stringify(details) }], details };
}

export function createRealCodeTools(sandbox: DockerRealCodeSandbox, generatedTestRoot: string): AgentTool[] {
	const list: AgentTool<typeof RepositoryListParameters, JsonValue> = {
		name: "repo_list",
		label: "List repository files",
		description:
			"List files and directories in the buggy repository. Paths are relative to /testbed; git metadata is hidden.",
		parameters: RepositoryListParameters,
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.list(params.path ?? ".", params.depth ?? 2));
		},
	};
	const read: AgentTool<typeof RepositoryReadParameters, JsonValue> = {
		name: "repo_read",
		label: "Read repository file",
		description:
			"Read a line-numbered excerpt from a source or test file in the buggy repository. Use start_line to continue long files.",
		parameters: RepositoryReadParameters,
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.read(params.path, params.start_line ?? 1, params.max_lines ?? 160));
		},
	};
	const search: AgentTool<typeof RepositorySearchParameters, JsonValue> = {
		name: "repo_search",
		label: "Search repository text",
		description:
			"Case-insensitive literal search across Python, configuration, and documentation files. Use it to find APIs and nearby test conventions.",
		parameters: RepositorySearchParameters,
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.search(params.path ?? ".", params.query));
		},
	};
	const write: AgentTool<typeof TestWriteParameters, JsonValue> = {
		name: "test_write",
		label: "Write generated regression test",
		description: `Create or replace a Python regression test under ${generatedTestRoot}/. Product source and existing upstream tests cannot be modified.`,
		parameters: TestWriteParameters,
		executionMode: "sequential",
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.writeTest(params.path, params.content));
		},
	};
	const run: AgentTool<typeof TestRunParameters, JsonValue> = {
		name: "test_run",
		label: "Run generated tests on buggy version",
		description: `Run all files under ${generatedTestRoot}/ against the supplied buggy commit. Exit 1 with focused assertion failures is expected when the target defect is reproduced; collection errors, timeouts, and unrelated failures are not valid evidence.`,
		parameters: TestRunParameters,
		executionMode: "sequential",
		async execute(_toolCallId, _params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.runAgentTests());
		},
	};
	return [list, read, search, write, run];
}
