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

const RepositoryWriteParameters = Type.Object({
	path: Type.String({ minLength: 1 }),
	content: Type.String({ minLength: 1, maxLength: 120_000 }),
});

const RepositoryReplaceParameters = Type.Object({
	path: Type.String({ minLength: 1 }),
	old_text: Type.String({ minLength: 1, maxLength: 60_000 }),
	new_text: Type.String({ maxLength: 60_000 }),
	expected_occurrences: Type.Optional(Type.Integer({ minimum: 1, maximum: 20, default: 1 })),
});

const RepositoryDeleteParameters = Type.Object({
	path: Type.String({ minLength: 1 }),
});

const RepositoryDiffParameters = Type.Object({});

const CommandRunParameters = Type.Object({
	argv: Type.Array(Type.String({ minLength: 1, maxLength: 2_000 }), { minItems: 1, maxItems: 80 }),
	timeout_ms: Type.Optional(Type.Integer({ minimum: 10_000, maximum: 180_000 })),
});

function textResult(value: unknown): AgentToolResult<JsonValue> {
	const details = toJsonValue(value);
	return { content: [{ type: "text", text: JSON.stringify(details) }], details };
}

export function createIssueTools(sandbox: DockerRealCodeSandbox): AgentTool[] {
	const list: AgentTool<typeof RepositoryListParameters, JsonValue> = {
		name: "repo_list",
		label: "List repository files",
		description:
			"List repository files and directories. Paths are relative to the repository; Git metadata is hidden.",
		parameters: RepositoryListParameters,
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.list(params.path ?? ".", params.depth ?? 2));
		},
	};
	const read: AgentTool<typeof RepositoryReadParameters, JsonValue> = {
		name: "repo_read",
		label: "Read repository file",
		description: "Read a line-numbered excerpt from a repository source, test, configuration, or documentation file.",
		parameters: RepositoryReadParameters,
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.read(params.path, params.start_line ?? 1, params.max_lines ?? 160));
		},
	};
	const search: AgentTool<typeof RepositorySearchParameters, JsonValue> = {
		name: "repo_search",
		label: "Search repository text",
		description: "Case-insensitive literal search across Python, configuration, test, and documentation files.",
		parameters: RepositorySearchParameters,
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.search(params.path ?? ".", params.query));
		},
	};
	const write: AgentTool<typeof RepositoryWriteParameters, JsonValue> = {
		name: "repo_write",
		label: "Write repository file",
		description:
			"Create or replace an authorized repository file. Product source and new tests are writable; existing tests, lock files, Git metadata, and evaluator assets are protected.",
		parameters: RepositoryWriteParameters,
		executionMode: "sequential",
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.writeRepositoryFile(params.path, params.content));
		},
	};
	const replace: AgentTool<typeof RepositoryReplaceParameters, JsonValue> = {
		name: "repo_replace",
		label: "Replace repository text",
		description:
			"Replace an exact text fragment in an authorized repository file. The operation fails unless the expected occurrence count matches.",
		parameters: RepositoryReplaceParameters,
		executionMode: "sequential",
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(
				await sandbox.replaceRepositoryText(
					params.path,
					params.old_text,
					params.new_text,
					params.expected_occurrences ?? 1,
				),
			);
		},
	};
	const remove: AgentTool<typeof RepositoryDeleteParameters, JsonValue> = {
		name: "repo_delete",
		label: "Delete new repository file",
		description:
			"Delete an untracked file created during this attempt. Tracked files, directories, lock files, Git metadata, and paths outside the repository cannot be deleted.",
		parameters: RepositoryDeleteParameters,
		executionMode: "sequential",
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.deleteRepositoryFile(params.path));
		},
	};
	const diff: AgentTool<typeof RepositoryDiffParameters, JsonValue> = {
		name: "repo_diff",
		label: "Inspect repository diff",
		description: "Show the current repository status and tracked diff for changes made during this attempt.",
		parameters: RepositoryDiffParameters,
		async execute(_toolCallId, _params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.repositoryDiff());
		},
	};
	const command: AgentTool<typeof CommandRunParameters, JsonValue> = {
		name: "command_run",
		label: "Run project command",
		description:
			"Run a bounded Python or pytest command from the repository root. Supply an argv array; shell syntax, inline Python, network tools, and protected paths are unavailable.",
		parameters: CommandRunParameters,
		executionMode: "sequential",
		async execute(_toolCallId, params, signal) {
			signal?.throwIfAborted();
			return textResult(await sandbox.runRepositoryCommand(params.argv, params.timeout_ms));
		},
	};
	return [list, read, search, write, replace, remove, diff, command];
}
