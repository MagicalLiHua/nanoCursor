import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { posix } from "node:path";
import type {
	IssueChangedFileArtifact,
	IssueGrade,
	IssueTask,
	IssueToolSmokeCheck,
	IssueToolSmokeResult,
} from "./issue-types.ts";
import type {
	GeneratedTestArtifact,
	ProcessResult,
	RealCodeGrade,
	RealCodeGradeCheck,
	RealCodePreflightResult,
	RealCodeTask,
} from "./types.ts";

const DEFAULT_TEST_ROOT = "testing/agent_generated";
const MAX_CAPTURE_BYTES = 64 * 1024;
const DEFAULT_TIMEOUT_MS = 180_000;

const READ_SCRIPT = `
from pathlib import Path
import sys

path = Path("/testbed") / sys.argv[1]
start = int(sys.argv[2])
limit = int(sys.argv[3])
lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
for number, line in enumerate(lines[start - 1:start - 1 + limit], start=start):
    print(f"{number:6d}\t{line}")
`;

const LIST_SCRIPT = `
from pathlib import Path
import sys

base = Path("/testbed")
root = base / sys.argv[1]
max_depth = int(sys.argv[2])
count = 0
for path in sorted(root.rglob("*")):
    relative_to_root = path.relative_to(root)
    if len(relative_to_root.parts) > max_depth:
        continue
    relative = path.relative_to(base)
    if ".git" in relative.parts:
        continue
    suffix = "/" if path.is_dir() else ""
    print(f"{relative}{suffix}")
    count += 1
    if count >= 240:
        print("... listing truncated ...")
        break
`;

const SEARCH_SCRIPT = `
from pathlib import Path
import sys

base = Path("/testbed")
root = base / sys.argv[1]
needle = sys.argv[2].casefold()
allowed_suffixes = {".py", ".toml", ".ini", ".cfg", ".rst", ".md", ".txt", ".yml", ".yaml"}
matches = 0
for path in sorted(root.rglob("*")):
    relative = path.relative_to(base)
    if not path.is_file() or ".git" in relative.parts or path.suffix.lower() not in allowed_suffixes:
        continue
    if path.stat().st_size > 1_000_000:
        continue
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if needle in line.casefold():
            print(f"{relative}:{number}:{line[:500]}")
            matches += 1
            if matches >= 120:
                print("... search truncated ...")
                raise SystemExit(0)
`;

const WRITE_SCRIPT = `
from pathlib import Path
import sys

path = Path("/testbed") / sys.argv[1]
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(sys.stdin.read(), encoding="utf-8")
`;

const REPLACE_SCRIPT = `
from pathlib import Path
import json
import sys

path = Path("/testbed") / sys.argv[1]
payload = json.loads(sys.stdin.read())
content = path.read_text(encoding="utf-8")
count = content.count(payload["old_text"])
if count != payload["expected_occurrences"]:
    raise SystemExit(f"expected {payload['expected_occurrences']} occurrence(s), found {count}")
path.write_text(content.replace(payload["old_text"], payload["new_text"]), encoding="utf-8")
`;

const DELETE_SCRIPT = `
from pathlib import Path
import sys

base = Path("/testbed").resolve()
path = base / sys.argv[1]
parent = path.parent.resolve()
if parent != base and base not in parent.parents:
    raise SystemExit("resolved path leaves the repository")
if not path.exists() and not path.is_symlink():
    raise SystemExit("file does not exist")
if path.is_dir() and not path.is_symlink():
    raise SystemExit("directories cannot be deleted")
path.unlink()
`;

const DISCOVER_SMOKE_FILES_SCRIPT = `
from pathlib import Path
import json
import subprocess
import sys

base = Path("/testbed")
paths = subprocess.check_output(["git", "ls-files", "-z", "--", "*.py"], cwd=str(base)).decode().split("\\0")
paths = [path for path in paths if path]

mode = sys.argv[1]

def is_test(path):
    parts = path.split("/")
    name = parts[-1]
    root_test_file = "/" not in path and name.startswith("test_") and name.endswith(".py")
    if mode == "root-test-files":
        return root_test_file
    if mode == "tests-root":
        return path.startswith("tests/") or root_test_file
    if mode == "testing-root":
        return path.startswith("testing/") or root_test_file
    return "tests" in parts or root_test_file

products = [path for path in paths if not is_test(path)]
product = next(
    (path for path in products if "def " in (base / path).read_text(encoding="utf-8", errors="replace")),
    next((path for path in products if (base / path).stat().st_size > 0), None),
)
test = next((path for path in paths if is_test(path)), None)
print(json.dumps({"product": product, "test": test}))
`;

const GENERATED_FILES_SCRIPT = `
from pathlib import Path
import sys

base = Path("/testbed")
root = base / sys.argv[1]
if root.exists():
    for path in sorted(root.rglob("test_*.py")):
        if path.is_file():
            print(path.relative_to(base))
`;

interface CommandOptions {
	env?: NodeJS.ProcessEnv;
	stdin?: string;
	timeoutMs?: number;
}

function appendTail(current: string, chunk: Buffer): string {
	const combined = current + chunk.toString("utf8");
	return combined.length <= MAX_CAPTURE_BYTES ? combined : combined.slice(-MAX_CAPTURE_BYTES);
}

async function runCommand(program: string, args: string[], options: CommandOptions = {}): Promise<ProcessResult> {
	const startedAt = Date.now();
	const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
	return new Promise<ProcessResult>((resolve, reject) => {
		const child = spawn(program, args, {
			env: options.env ?? process.env,
			stdio: ["pipe", "pipe", "pipe"],
		});
		let stdout = "";
		let stderr = "";
		let timedOut = false;
		const timer = setTimeout(() => {
			timedOut = true;
			child.kill("SIGKILL");
		}, timeoutMs);
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
		child.stdin.end(options.stdin);
	});
}

function requireSuccess(result: ProcessResult, operation: string): void {
	if (result.exitCode === 0 && !result.timedOut) return;
	throw new Error(
		`${operation} failed with exit ${result.exitCode}${result.timedOut ? " after timeout" : ""}:\n${result.stderr || result.stdout}`,
	);
}

export function normalizeRepositoryPath(input: string): string {
	const trimmed = input.trim();
	if (!trimmed || trimmed === ".") return ".";
	if (trimmed.startsWith("/")) throw new Error("Repository paths must be relative.");
	const normalized = posix.normalize(trimmed);
	if (normalized === ".." || normalized.startsWith("../")) throw new Error("Path traversal is not allowed.");
	if (normalized === ".git" || normalized.startsWith(".git/")) throw new Error("Git metadata is not exposed.");
	return normalized.replace(/^\.\//, "");
}

export function normalizeGeneratedTestPath(input: string, testRoot = DEFAULT_TEST_ROOT): string {
	const normalizedRoot = normalizeRepositoryPath(testRoot);
	const normalized = normalizeRepositoryPath(input);
	if (!normalized.startsWith(`${normalizedRoot}/`)) {
		throw new Error(`Generated tests must be written under ${normalizedRoot}/.`);
	}
	if (!normalized.endsWith(".py") || !posix.basename(normalized).startsWith("test_")) {
		throw new Error("Generated test filenames must start with test_ and end with .py.");
	}
	return normalized;
}

const DEPENDENCY_LOCK_FILES = new Set([
	"package-lock.json",
	"npm-shrinkwrap.json",
	"pnpm-lock.yaml",
	"yarn.lock",
	"poetry.lock",
	"pdm.lock",
	"uv.lock",
	"Pipfile.lock",
]);

export function isDependencyLockPath(path: string): boolean {
	return DEPENDENCY_LOCK_FILES.has(posix.basename(path));
}

export function isRepositoryTestPath(path: string): boolean {
	const parts = path.split("/");
	const name = posix.basename(path);
	return parts.some((part) => part === "test" || part === "tests" || part === "testing") || /^test_.*\.py$/.test(name);
}

export function isIssueTaskTestPath(task: IssueTask, path: string): boolean {
	const normalized = normalizeRepositoryPath(path);
	const name = posix.basename(normalized);
	const rootTestFile = !normalized.includes("/") && /^test_.*\.py$/.test(name);
	if (task.testPathMode === "root-test-files") return rootTestFile;
	if (task.testPathMode === "tests-root") return normalized.startsWith("tests/") || rootTestFile;
	if (task.testPathMode === "testing-root") return normalized.startsWith("testing/") || rootTestFile;
	return normalized.split("/").includes("tests") || rootTestFile;
}

export function isRuntimeArtifactPath(path: string): boolean {
	const normalized = normalizeRepositoryPath(path);
	const parts = normalized.split("/");
	const name = posix.basename(normalized);
	return (
		parts[0] === "build" ||
		parts[0] === "dist" ||
		parts.includes("__pycache__") ||
		parts.includes(".pytest_cache") ||
		parts.includes(".tox") ||
		parts.includes(".nox") ||
		parts.includes(".eggs") ||
		name === ".coverage" ||
		name.endsWith(".pyc") ||
		name.endsWith(".pyo") ||
		name.endsWith(".egg-info")
	);
}

export function parseGitApplyNumstat(output: string): string[] {
	return output
		.split(/\r?\n/)
		.map((line) => line.trimEnd())
		.filter((line) => line.length > 0)
		.map((line) => line.split("\t").slice(2).join("\t"))
		.filter((path) => path.length > 0)
		.map((path) => normalizeRepositoryPath(path));
}

export function validateIssueDeletePath(path: string, tracked: boolean): string {
	const normalized = normalizeRepositoryPath(path);
	if (isDependencyLockPath(normalized)) throw new Error("Dependency lock files are read-only.");
	if (tracked) throw new Error("Tracked repository files cannot be deleted.");
	return normalized;
}

export function buildIssueAgentTestCommand(task: IssueTask, paths: string[]): string {
	const arguments_ = paths.map((path) => {
		const normalized = normalizeRepositoryPath(path);
		if (task.newTestPathMode === "django-label") {
			if (!normalized.startsWith("tests/") || !normalized.endsWith(".py")) {
				throw new Error("Django Agent tests must be Python files under tests/.");
			}
			return normalized.slice("tests/".length, -".py".length).replaceAll("/", ".");
		}
		return normalized;
	});
	return `${task.newTestCommandPrefix} ${arguments_.map((argument) => `'${argument.replaceAll("'", "'\\''")}'`).join(" ")}`;
}

export function validateIssueCommand(argv: string[]): string[] {
	if (argv.length === 0) throw new Error("Command argv must not be empty.");
	if (argv.length > 80) throw new Error("Command argv must not contain more than 80 entries.");
	const allowedPrograms = new Set([
		"python",
		"python3",
		"/opt/miniconda3/envs/testbed/bin/python",
		"pytest",
		"py.test",
		"/opt/miniconda3/envs/testbed/bin/pytest",
	]);
	if (!allowedPrograms.has(argv[0] ?? "")) {
		throw new Error("Only Python and pytest project commands are available.");
	}
	for (const argument of argv) {
		if (argument.length > 2_000) throw new Error("Command arguments must not exceed 2000 characters.");
		if (argument.startsWith("/")) {
			if (!allowedPrograms.has(argument)) throw new Error("Absolute command paths are not allowed.");
		}
		if (/\.git(?:\/|$)|gold\.patch|upstream-tests\.patch|evaluator|grader/i.test(argument)) {
			throw new Error("Command arguments must not reference protected evaluator or Git assets.");
		}
	}
	const program = argv[0] ?? "";
	if (program.endsWith("python") || program.endsWith("python3")) {
		if (argv[1] === "-c" || argv[1] === "-") throw new Error("Inline Python execution is not allowed.");
		if (argv[1] === "-m" && argv[2] !== "pytest") throw new Error("Only python -m pytest is allowed.");
	}
	const normalized = [...argv];
	if (program === "python" || program === "python3") {
		normalized[0] = "/opt/miniconda3/envs/testbed/bin/python";
	} else if (program === "pytest" || program === "py.test") {
		normalized.splice(0, 1, "/opt/miniconda3/envs/testbed/bin/python", "-m", "pytest");
	}
	return normalized;
}

function dockerEnvironment(dockerHost?: string): NodeJS.ProcessEnv {
	const env: NodeJS.ProcessEnv = { ...process.env };
	if (dockerHost?.trim()) env.DOCKER_HOST = dockerHost.trim();
	return env;
}

function parseUnexpectedChanges(status: string, testRoot: string): string[] {
	return status
		.split(/\r?\n/)
		.map((line) => line.trimEnd())
		.filter((line) => line.length > 0)
		.filter((line) => {
			const path = line.length > 3 ? line.slice(3).trim() : "";
			return path !== testRoot && !path.startsWith(`${testRoot}/`);
		});
}

export function findSuspiciousPatterns(contents: string[]): string[] {
	const combined = contents.join("\n");
	const patterns: Array<[string, RegExp]> = [
		["version-probe", /__version__|version_info|importlib\.metadata\.version|get_distribution\s*\(/i],
		[
			"git-metadata-probe",
			/git\s+(?:rev-parse|show|diff|log)|["']git["']\s*,\s*["'](?:rev-parse|show|diff|log)["']|\.git(?:\/|["'])/i,
		],
		[
			"hidden-evaluator-probe",
			/gold\.patch|upstream-tests\.patch|(?:^|\/)evaluator(?:\/|["'])|(?:^|\/)grader(?:\/|["'])/im,
		],
		["source-text-probe", /inspect\.getsource|read_text\s*\(|src\/_pytest/i],
		["unconditional-failure", /^\s*assert\s+False\b/m],
		["unconditional-skip", /pytest\.(?:skip|xfail)\s*\(/],
	];
	return patterns.filter(([, pattern]) => pattern.test(combined)).map(([name]) => name);
}

function isAssertionFailure(result: ProcessResult): boolean {
	if (result.exitCode !== 1 || result.timedOut) return false;
	const output = `${result.stdout}\n${result.stderr}`;
	const invalidFailure =
		/ERROR collecting|errors? during collection|ImportError|ModuleNotFoundError|SyntaxError|IndentationError|TabError|INTERNALERROR|no tests ran|collected 0 items/i.test(
			output,
		);
	if (invalidFailure) return false;
	return /AssertionError|^E\s+assert\b|^E\s+Failed:/m.test(output);
}

export function evaluateRealCodeGrade(input: {
	generatedFiles: string[];
	generatedTests?: GeneratedTestArtifact[];
	unexpectedChanges: string[];
	suspiciousPatterns: string[];
	buggyRun: ProcessResult;
	fixedRun: ProcessResult;
	regressionRun: ProcessResult;
}): RealCodeGrade {
	const checks: RealCodeGradeCheck[] = [
		{
			id: "generated-tests-present",
			passed: input.generatedFiles.length > 0,
			message: `${input.generatedFiles.length} generated test file(s).`,
		},
		{
			id: "product-source-unchanged",
			passed: input.unexpectedChanges.length === 0,
			message:
				input.unexpectedChanges.length === 0
					? "Only the dedicated generated-test directory changed."
					: `Unexpected changes: ${input.unexpectedChanges.join(", ")}`,
		},
		{
			id: "bug-reproduced",
			passed: input.buggyRun.exitCode === 1 && !input.buggyRun.timedOut,
			message: `Buggy run exited ${input.buggyRun.exitCode}${input.buggyRun.timedOut ? " after timeout" : ""}.`,
		},
		{
			id: "buggy-failure-is-assertion",
			passed: isAssertionFailure(input.buggyRun),
			message: isAssertionFailure(input.buggyRun)
				? "Buggy run contains an assertion-level failure without collection, import, or syntax errors."
				: "Buggy run must contain an assertion-level failure without collection, import, syntax, or timeout errors.",
		},
		{
			id: "maintainer-fix-verified",
			passed: input.fixedRun.exitCode === 0 && !input.fixedRun.timedOut,
			message: `Fixed run exited ${input.fixedRun.exitCode}${input.fixedRun.timedOut ? " after timeout" : ""}.`,
		},
		{
			id: "upstream-regression-safe",
			passed: input.regressionRun.exitCode === 0 && !input.regressionRun.timedOut,
			message: `Regression run exited ${input.regressionRun.exitCode}${input.regressionRun.timedOut ? " after timeout" : ""}.`,
		},
		{
			id: "anti-cheat-static-scan",
			passed: input.suspiciousPatterns.length === 0,
			message:
				input.suspiciousPatterns.length === 0
					? "No version, git metadata, source-text, or unconditional-skip probes detected."
					: `Suspicious patterns: ${input.suspiciousPatterns.join(", ")}`,
		},
	];
	return {
		passed: checks.every((check) => check.passed),
		checks,
		generatedFiles: [...input.generatedFiles],
		generatedTests: structuredClone(input.generatedTests ?? []),
		unexpectedChanges: [...input.unexpectedChanges],
		suspiciousPatterns: [...input.suspiciousPatterns],
		buggyRun: input.buggyRun,
		fixedRun: input.fixedRun,
		regressionRun: input.regressionRun,
	};
}

export function evaluateIssueGrade(input: {
	repositoryStatus: string;
	finalPatch: string;
	changedFiles: IssueChangedFileArtifact[];
	forbiddenChanges: string[];
	productChangeCount: number;
	evaluatorSetupRun: ProcessResult;
	agentTestsRun: ProcessResult;
	targetRun: ProcessResult;
	regressionRun: ProcessResult;
}): IssueGrade {
	const checks = [
		{
			id: "repository-changes-present",
			passed: input.changedFiles.length > 0,
			message: `${input.changedFiles.length} changed file(s) captured.`,
		},
		{
			id: "product-change-present",
			passed: input.productChangeCount > 0,
			message: `${input.productChangeCount} product or configuration file change(s) captured.`,
		},
		{
			id: "protected-files-unchanged",
			passed: input.forbiddenChanges.length === 0,
			message:
				input.forbiddenChanges.length === 0
					? "No existing tests or dependency lock files were changed."
					: `Forbidden changes: ${input.forbiddenChanges.join(", ")}`,
		},
		{
			id: "agent-added-tests-pass",
			passed: input.agentTestsRun.exitCode === 0 && !input.agentTestsRun.timedOut,
			message: `Agent-added test validation exited ${input.agentTestsRun.exitCode}${input.agentTestsRun.timedOut ? " after timeout" : ""}.`,
		},
		{
			id: "hidden-tests-injected",
			passed: input.evaluatorSetupRun.exitCode === 0 && !input.evaluatorSetupRun.timedOut,
			message: `Evaluator setup exited ${input.evaluatorSetupRun.exitCode}${input.evaluatorSetupRun.timedOut ? " after timeout" : ""}.`,
		},
		{
			id: "target-behavior-resolved",
			passed: input.targetRun.exitCode === 0 && !input.targetRun.timedOut,
			message: `Target validation exited ${input.targetRun.exitCode}${input.targetRun.timedOut ? " after timeout" : ""}.`,
		},
		{
			id: "related-regression-safe",
			passed: input.regressionRun.exitCode === 0 && !input.regressionRun.timedOut,
			message: `Regression validation exited ${input.regressionRun.exitCode}${input.regressionRun.timedOut ? " after timeout" : ""}.`,
		},
	];
	return {
		passed: checks.every((check) => check.passed),
		checks,
		repositoryStatus: input.repositoryStatus,
		finalPatch: input.finalPatch,
		changedFiles: structuredClone(input.changedFiles),
		forbiddenChanges: [...input.forbiddenChanges],
		evaluatorSetupRun: input.evaluatorSetupRun,
		agentTestsRun: input.agentTestsRun,
		targetRun: input.targetRun,
		regressionRun: input.regressionRun,
	};
}

export class DockerRealCodeSandbox {
	private readonly task: RealCodeTask | IssueTask;
	private readonly env: NodeJS.ProcessEnv;
	private readonly commandTimeoutMs: number;
	private readonly containerName: string;
	private started = false;

	constructor(
		task: RealCodeTask | IssueTask,
		options: { dockerHost?: string; commandTimeoutMs?: number; runId?: string } = {},
	) {
		this.task = task;
		this.env = dockerEnvironment(options.dockerHost);
		this.commandTimeoutMs = options.commandTimeoutMs ?? DEFAULT_TIMEOUT_MS;
		const runId = options.runId ?? `${task.id}-${randomUUID().slice(0, 8)}`;
		this.containerName = `agent-eval-${runId}`.replace(/[^a-zA-Z0-9_.-]/g, "-").slice(0, 120);
	}

	async start(): Promise<void> {
		if (this.started) throw new Error("Real-code sandbox is already started.");
		const created = await runCommand(
			"docker",
			[
				"run",
				"-d",
				"--pull",
				"never",
				"--name",
				this.containerName,
				"--network",
				"none",
				"--cpus",
				"2",
				"--memory",
				"4g",
				"--pids-limit",
				"512",
				"--security-opt",
				"no-new-privileges:true",
				"--label",
				"nanocursor-agent-eval=real-code-v1",
				this.task.image,
				"sleep",
				"infinity",
			],
			{ env: this.env, timeoutMs: 60_000 },
		);
		requireSuccess(created, `Create container ${this.containerName}`);
		this.started = true;
		try {
			const reset = await this.exec(["git", "reset", "--hard", this.task.baseCommit]);
			requireSuccess(reset, `Reset ${this.task.id} to base commit`);
			if ("generatedTestRoot" in this.task) {
				const mkdir = await this.exec(["mkdir", "-p", this.task.generatedTestRoot]);
				requireSuccess(mkdir, `Create ${this.task.generatedTestRoot}`);
				const packageMarker = await this.exec(["touch", `${this.task.generatedTestRoot}/__init__.py`]);
				requireSuccess(packageMarker, `Initialize ${this.task.generatedTestRoot} as a test package`);
			}
		} catch (error) {
			await this.close();
			throw error;
		}
	}

	async close(): Promise<void> {
		if (!this.started) return;
		await runCommand("docker", ["rm", "-f", this.containerName], { env: this.env, timeoutMs: 60_000 });
		this.started = false;
	}

	async list(path: string, depth: number): Promise<string> {
		const normalized = normalizeRepositoryPath(path);
		const boundedDepth = Math.max(1, Math.min(4, Math.trunc(depth)));
		const result = await this.python(LIST_SCRIPT, [normalized, String(boundedDepth)]);
		requireSuccess(result, `List ${normalized}`);
		return result.stdout;
	}

	async read(path: string, startLine: number, maxLines: number): Promise<string> {
		const normalized = normalizeRepositoryPath(path);
		const start = Math.max(1, Math.trunc(startLine));
		const limit = Math.max(1, Math.min(240, Math.trunc(maxLines)));
		const result = await this.python(READ_SCRIPT, [normalized, String(start), String(limit)]);
		requireSuccess(result, `Read ${normalized}`);
		return result.stdout;
	}

	async search(path: string, query: string): Promise<string> {
		const normalized = normalizeRepositoryPath(path);
		const needle = query.trim();
		if (!needle) throw new Error("Search query must not be empty.");
		if (needle.length > 200) throw new Error("Search query must be at most 200 characters.");
		const result = await this.python(SEARCH_SCRIPT, [normalized, needle]);
		requireSuccess(result, `Search ${normalized}`);
		return result.stdout || "No matches.";
	}

	async writeTest(path: string, content: string): Promise<{ path: string; bytes: number; sha256: string }> {
		if (!("generatedTestRoot" in this.task))
			throw new Error("Generated-test writes are unavailable for issue tasks.");
		const normalized = normalizeGeneratedTestPath(path, this.task.generatedTestRoot);
		const bytes = Buffer.byteLength(content, "utf8");
		if (bytes === 0) throw new Error("Generated test content must not be empty.");
		if (bytes > 60_000) throw new Error("Generated test content must not exceed 60 KB.");
		const result = await this.python(WRITE_SCRIPT, [normalized], content);
		requireSuccess(result, `Write ${normalized}`);
		return { path: normalized, bytes, sha256: createHash("sha256").update(content).digest("hex") };
	}

	async writeRepositoryFile(path: string, content: string): Promise<{ path: string; bytes: number; sha256: string }> {
		const normalized = await this.authorizeIssueWrite(path);
		const bytes = Buffer.byteLength(content, "utf8");
		if (bytes === 0) throw new Error("Repository file content must not be empty.");
		if (bytes > 120_000) throw new Error("Repository file content must not exceed 120 KB.");
		const result = await this.python(WRITE_SCRIPT, [normalized], content);
		requireSuccess(result, `Write ${normalized}`);
		return { path: normalized, bytes, sha256: createHash("sha256").update(content).digest("hex") };
	}

	async replaceRepositoryText(
		path: string,
		oldText: string,
		newText: string,
		expectedOccurrences: number,
	): Promise<{ path: string; replacements: number }> {
		const normalized = await this.authorizeIssueWrite(path);
		if (!oldText) throw new Error("old_text must not be empty.");
		if (Buffer.byteLength(oldText, "utf8") > 60_000 || Buffer.byteLength(newText, "utf8") > 60_000) {
			throw new Error("Replacement text must not exceed 60 KB.");
		}
		if (!Number.isInteger(expectedOccurrences) || expectedOccurrences < 1 || expectedOccurrences > 20) {
			throw new Error("expected_occurrences must be an integer from 1 to 20.");
		}
		const payload = JSON.stringify({
			old_text: oldText,
			new_text: newText,
			expected_occurrences: expectedOccurrences,
		});
		const result = await this.python(REPLACE_SCRIPT, [normalized], payload);
		requireSuccess(result, `Replace text in ${normalized}`);
		return { path: normalized, replacements: expectedOccurrences };
	}

	async deleteRepositoryFile(path: string): Promise<{ path: string; deleted: true }> {
		if ("generatedTestRoot" in this.task) throw new Error("Repository deletes are unavailable for legacy tasks.");
		const normalized = normalizeRepositoryPath(path);
		const tracked = await this.exec(["git", "ls-files", "--error-unmatch", "--", normalized]);
		validateIssueDeletePath(normalized, tracked.exitCode === 0);
		const result = await this.python(DELETE_SCRIPT, [normalized]);
		requireSuccess(result, `Delete ${normalized}`);
		return { path: normalized, deleted: true };
	}

	async repositoryDiff(): Promise<string> {
		const status = await this.exec(["git", "status", "--short", "--untracked-files=all"]);
		requireSuccess(status, "Inspect repository status");
		const diff = await this.exec(["git", "diff", "--no-ext-diff", "--", "."]);
		requireSuccess(diff, "Inspect repository diff");
		return `STATUS\n${status.stdout || "(clean)"}\nDIFF\n${diff.stdout || "(no tracked diff)"}`;
	}

	async runRepositoryCommand(argv: string[], timeoutMs?: number): Promise<ProcessResult> {
		const command = validateIssueCommand(argv);
		const boundedTimeout = Math.max(10_000, Math.min(this.commandTimeoutMs, timeoutMs ?? this.commandTimeoutMs));
		const timeoutSeconds = Math.max(1, Math.ceil(boundedTimeout / 1_000));
		return this.exec(["timeout", `${timeoutSeconds}s`, ...command], undefined, boundedTimeout + 10_000);
	}

	async smokeIssueTools(): Promise<IssueToolSmokeResult> {
		if ("generatedTestRoot" in this.task) throw new Error("Issue tool smoke requires an issue task.");
		if (!this.task.toolSmokeArgv) throw new Error(`No tool smoke command is configured for ${this.task.id}.`);
		const checks: IssueToolSmokeCheck[] = [];
		const record = (id: string, passed: boolean, message: string): void => {
			checks.push({ id, passed, message });
		};
		const errorMessage = (error: unknown): string => (error instanceof Error ? error.message : String(error));
		const discovered = await this.python(DISCOVER_SMOKE_FILES_SCRIPT, [this.task.testPathMode]);
		requireSuccess(discovered, "Discover tracked Python files");
		const smokeFiles = JSON.parse(discovered.stdout) as { product: string | null; test: string | null };
		const productFile = smokeFiles.product ?? undefined;
		const testFile = smokeFiles.test ?? undefined;
		record(
			"tracked-product-discovered",
			productFile !== undefined,
			productFile ?? "No tracked product Python file found.",
		);
		record("tracked-test-discovered", testFile !== undefined, testFile ?? "No tracked test Python file found.");

		try {
			const listing = await this.list(".", 1);
			record("repository-list", listing.trim().length > 0, `${listing.split(/\r?\n/).length} listed entries.`);
		} catch (error) {
			record("repository-list", false, errorMessage(error));
		}
		if (productFile) {
			try {
				const content = await this.read(productFile, 1, 20);
				record("repository-read", content.trim().length > 0, `Read ${productFile}.`);
			} catch (error) {
				record("repository-read", false, errorMessage(error));
			}
			try {
				const matches = await this.search(posix.dirname(productFile), "def ");
				record("repository-search", matches !== "No matches.", `Searched ${posix.dirname(productFile)}.`);
			} catch (error) {
				record("repository-search", false, errorMessage(error));
			}
			try {
				await this.deleteRepositoryFile(productFile);
				record("tracked-product-delete-blocked", false, `Unexpectedly deleted ${productFile}.`);
			} catch (error) {
				record(
					"tracked-product-delete-blocked",
					/cannot be deleted/i.test(errorMessage(error)),
					errorMessage(error),
				);
			}
		}
		if (testFile) {
			try {
				await this.writeRepositoryFile(testFile, "# forbidden smoke write\n");
				record("tracked-test-write-blocked", false, `Unexpectedly wrote ${testFile}.`);
			} catch (error) {
				record("tracked-test-write-blocked", /read-only/i.test(errorMessage(error)), errorMessage(error));
			}
		}

		const smokePath = ".agent_eval_smoke.py";
		try {
			await this.writeRepositoryFile(smokePath, 'SMOKE_VALUE = "测试"\n');
			record("untracked-file-write", true, `Wrote ${smokePath}.`);
			const unicodeContent = await this.read(smokePath, 1, 20);
			record("unicode-repository-read", unicodeContent.includes("测试"), `Read UTF-8 content from ${smokePath}.`);
			await this.replaceRepositoryText(smokePath, 'SMOKE_VALUE = "测试"', 'SMOKE_VALUE = "通过"', 1);
			record("untracked-file-replace", true, `Replaced text in ${smokePath}.`);
			const diff = await this.repositoryDiff();
			record("repository-diff", diff.includes(smokePath), `Diff captured ${smokePath}.`);
			await this.deleteRepositoryFile(smokePath);
			record("untracked-file-delete", true, `Deleted ${smokePath}.`);
		} catch (error) {
			record("untracked-file-lifecycle", false, errorMessage(error));
			const exists = await this.exec(["test", "-e", smokePath]);
			if (exists.exitCode === 0) await this.exec(["rm", "-f", "--", smokePath]);
		}

		let commandRun: ProcessResult | undefined;
		try {
			commandRun = await this.runRepositoryCommand(this.task.toolSmokeArgv);
			record(
				"project-command",
				commandRun.exitCode === 0 && !commandRun.timedOut,
				`Configured smoke command exited ${commandRun.exitCode}${commandRun.timedOut ? " after timeout" : ""}.`,
			);
		} catch (error) {
			record("project-command", false, errorMessage(error));
		}
		try {
			const finalStatus = await this.exec(["git", "status", "--short", "--untracked-files=all"]);
			requireSuccess(finalStatus, "Inspect smoke cleanup status");
			const remaining = finalStatus.stdout
				.split(/\r?\n/)
				.map((line) => line.trimEnd())
				.filter((line) => line.length > 0)
				.filter(
					(line) => line.slice(0, 2) !== "??" || !isRuntimeArtifactPath(line.slice(3).split(" -> ").at(-1) ?? ""),
				);
			const finalDiff = await this.exec(["git", "diff", "--no-ext-diff", "--", "."]);
			requireSuccess(finalDiff, "Inspect smoke cleanup diff");
			record(
				"repository-cleanup",
				remaining.length === 0 && finalDiff.stdout.trim().length === 0,
				remaining.length === 0
					? "No Agent-authored changes remain; command-generated runtime artifacts are ignored."
					: `Unexpected residual changes: ${remaining.join(", ")}`,
			);
		} catch (error) {
			record("repository-cleanup", false, errorMessage(error));
		}
		return {
			taskId: this.task.id,
			instanceId: this.task.instanceId,
			image: this.task.image,
			baseCommit: this.task.baseCommit,
			passed: checks.every((check) => check.passed),
			checks,
			...(commandRun ? { commandRun } : {}),
		};
	}

	async runAgentTests(): Promise<ProcessResult> {
		if (!("agentTestCommand" in this.task))
			throw new Error("Generated-test execution is unavailable for issue tasks.");
		return this.runTrustedTestCommand(this.task.agentTestCommand);
	}

	async grade(): Promise<RealCodeGrade> {
		if (!("generatedTestRoot" in this.task))
			throw new Error("Legacy real-code grading is unavailable for issue tasks.");
		const generatedFiles = await this.generatedFiles();
		const status = await this.exec(["git", "status", "--porcelain"]);
		requireSuccess(status, "Inspect repository changes");
		const unexpectedChanges = parseUnexpectedChanges(status.stdout, this.task.generatedTestRoot);
		const generatedTests = await Promise.all(
			generatedFiles.map(async (path): Promise<GeneratedTestArtifact> => {
				const content = await this.readWhole(path);
				return {
					path,
					bytes: Buffer.byteLength(content, "utf8"),
					sha256: createHash("sha256").update(content).digest("hex"),
					content,
				};
			}),
		);
		const suspiciousPatterns = findSuspiciousPatterns(generatedTests.map((test) => test.content));
		const buggyRun = await this.runAgentTests();
		await this.applyPatch(this.task.goldPatchPath);
		const fixedRun = await this.runAgentTests();
		const regressionRun = await this.runTrustedTestCommand(this.task.regressionCommand);
		return evaluateRealCodeGrade({
			generatedFiles,
			generatedTests,
			unexpectedChanges,
			suspiciousPatterns,
			buggyRun,
			fixedRun,
			regressionRun,
		});
	}

	async gradeIssue(): Promise<IssueGrade> {
		if ("generatedTestRoot" in this.task) throw new Error("Issue grading requires an issue task.");
		const task = this.task;
		const status = await this.exec(["git", "status", "--porcelain=v1", "--untracked-files=all"]);
		requireSuccess(status, "Inspect final repository status");
		const entries = status.stdout
			.split(/\r?\n/)
			.map((line) => line.trimEnd())
			.filter((line) => line.length > 0)
			.map((line) => ({ status: line.slice(0, 2), path: line.slice(3).split(" -> ").at(-1) ?? "" }))
			.filter((entry) => entry.status !== "??" || !isRuntimeArtifactPath(entry.path));
		const forbiddenChanges = entries
			.filter(
				({ status: fileStatus, path }) =>
					isDependencyLockPath(path) || (fileStatus !== "??" && isIssueTaskTestPath(task, path)),
			)
			.map(({ status: fileStatus, path }) => `${fileStatus} ${path}`);
		const changedFiles: IssueChangedFileArtifact[] = [];
		for (const entry of entries) {
			const exists = await this.exec(["test", "-f", entry.path]);
			const content = exists.exitCode === 0 ? await this.readWhole(entry.path) : "";
			changedFiles.push({
				path: entry.path,
				status: entry.status,
				bytes: Buffer.byteLength(content, "utf8"),
				sha256: createHash("sha256").update(content).digest("hex"),
				content,
			});
		}
		const untracked = entries.filter((entry) => entry.status === "??").map((entry) => entry.path);
		if (untracked.length > 0) {
			const intentToAdd = await this.exec(["git", "add", "-N", "--", ...untracked]);
			requireSuccess(intentToAdd, "Prepare untracked files for patch capture");
		}
		const patch = await this.exec(["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--", "."]);
		requireSuccess(patch, "Capture final Agent patch");
		const newTests = entries
			.filter(
				(entry) => entry.status === "??" && isIssueTaskTestPath(task, entry.path) && entry.path.endsWith(".py"),
			)
			.map((entry) => entry.path);
		const agentTestsRun =
			newTests.length > 0
				? await this.runTrustedTestCommand(buildIssueAgentTestCommand(task, newTests))
				: {
						exitCode: 0,
						stdout: "No new Agent tests to run.",
						stderr: "",
						durationMs: 0,
						timedOut: false,
					};
		const evaluatorPatchPaths = await this.patchPaths(task.upstreamTestPatchPath);
		const collidingAgentFiles = untracked.filter(
			(path) => evaluatorPatchPaths.includes(path) && isIssueTaskTestPath(task, path),
		);
		for (const path of collidingAgentFiles) {
			const removed = await this.python(DELETE_SCRIPT, [path]);
			requireSuccess(removed, `Temporarily remove colliding Agent test ${path}`);
		}
		const setupRun = await this.applyPatchResult(task.upstreamTestPatchPath);
		const evaluatorSetupRun =
			collidingAgentFiles.length === 0
				? setupRun
				: {
						...setupRun,
						stdout: `Temporarily removed ${collidingAgentFiles.length} captured Agent test file(s) that overlapped evaluator-created paths.\n${setupRun.stdout}`,
					};
		const targetRun =
			evaluatorSetupRun.exitCode === 0 && !evaluatorSetupRun.timedOut
				? await this.runTrustedTestCommand(task.hiddenTestCommand)
				: {
						exitCode: 125,
						stdout: "",
						stderr: "Target validation was not run because the evaluator test patch could not be applied.",
						durationMs: 0,
						timedOut: false,
					};
		const regressionRun = await this.runTrustedTestCommand(task.regressionCommand);
		return evaluateIssueGrade({
			repositoryStatus: status.stdout,
			finalPatch: patch.stdout,
			changedFiles,
			forbiddenChanges,
			productChangeCount: entries.filter((entry) => !isIssueTaskTestPath(task, entry.path)).length,
			evaluatorSetupRun,
			agentTestsRun,
			targetRun,
			regressionRun,
		});
	}

	async preflight(): Promise<RealCodePreflightResult> {
		await this.applyPatch(this.task.upstreamTestPatchPath);
		const buggyHiddenRun = await this.runTrustedTestCommand(this.task.hiddenTestCommand);
		await this.applyPatch(this.task.goldPatchPath);
		const fixedHiddenRun = await this.runTrustedTestCommand(this.task.hiddenTestCommand);
		const regressionRun = await this.runTrustedTestCommand(this.task.regressionCommand);
		return {
			taskId: this.task.id,
			image: this.task.image,
			baseCommit: this.task.baseCommit,
			buggyHiddenRun,
			fixedHiddenRun,
			regressionRun,
			passed:
				buggyHiddenRun.exitCode === 1 &&
				!buggyHiddenRun.timedOut &&
				fixedHiddenRun.exitCode === 0 &&
				!fixedHiddenRun.timedOut &&
				regressionRun.exitCode === 0 &&
				!regressionRun.timedOut,
		};
	}

	private async generatedFiles(): Promise<string[]> {
		if (!("generatedTestRoot" in this.task)) throw new Error("Generated tests are unavailable for issue tasks.");
		const generatedTestRoot = this.task.generatedTestRoot;
		const result = await this.python(GENERATED_FILES_SCRIPT, [generatedTestRoot]);
		requireSuccess(result, "List generated tests");
		return result.stdout
			.split(/\r?\n/)
			.map((line) => line.trim())
			.filter((line) => line.length > 0);
	}

	private async applyPatch(path: string): Promise<void> {
		const result = await this.applyPatchResult(path);
		requireSuccess(result, `Apply evaluator patch ${posix.basename(path)}`);
	}

	private async applyPatchResult(path: string): Promise<ProcessResult> {
		const patch = await readFile(path, "utf8");
		return this.exec(["git", "apply", "-"], patch);
	}

	private async patchPaths(path: string): Promise<string[]> {
		const patch = await readFile(path, "utf8");
		const result = await this.exec(["git", "apply", "--numstat", "-"], patch);
		requireSuccess(result, `Inspect evaluator patch ${posix.basename(path)}`);
		return parseGitApplyNumstat(result.stdout);
	}

	private async authorizeIssueWrite(path: string): Promise<string> {
		if ("generatedTestRoot" in this.task) throw new Error("Repository writes are unavailable for legacy tasks.");
		const normalized = normalizeRepositoryPath(path);
		if (isDependencyLockPath(normalized)) throw new Error("Dependency lock files are read-only.");
		if (isIssueTaskTestPath(this.task, normalized)) {
			const tracked = await this.exec(["git", "ls-files", "--error-unmatch", "--", normalized]);
			if (tracked.exitCode === 0)
				throw new Error("Existing repository tests are read-only; add a new test file instead.");
		}
		return normalized;
	}

	private async runTrustedTestCommand(command: string): Promise<ProcessResult> {
		const timeoutSeconds = Math.max(1, Math.ceil(this.commandTimeoutMs / 1000));
		return this.exec(
			["bash", "-lc", `timeout ${timeoutSeconds}s ${command}`],
			undefined,
			this.commandTimeoutMs + 10_000,
		);
	}

	private async python(script: string, args: string[], stdin?: string): Promise<ProcessResult> {
		return this.exec(
			["/usr/bin/env", "PYTHONIOENCODING=utf-8", "/opt/miniconda3/envs/testbed/bin/python", "-c", script, ...args],
			stdin,
		);
	}

	private async readWhole(path: string): Promise<string> {
		const normalized = normalizeRepositoryPath(path);
		const result = await this.python(
			'from pathlib import Path; import sys; sys.stdout.write((Path("/testbed") / sys.argv[1]).read_text(encoding="utf-8", errors="replace"))',
			[normalized],
		);
		requireSuccess(result, `Read generated test ${normalized}`);
		return result.stdout;
	}

	private async exec(args: string[], stdin?: string, timeoutMs?: number): Promise<ProcessResult> {
		if (!this.started) throw new Error("Real-code sandbox is not started.");
		return runCommand("docker", ["exec", ...(stdin !== undefined ? ["-i"] : []), this.containerName, ...args], {
			env: this.env,
			...(stdin !== undefined ? { stdin } : {}),
			timeoutMs: timeoutMs ?? this.commandTimeoutMs,
		});
	}
}
