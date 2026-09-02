import { getEvaluationIssueTasks } from "./evaluation-issue-tasks.ts";
import type { IssueTask } from "./issue-types.ts";
import { getRealCodeTask } from "./tasks.ts";

interface IssueTaskDefinition {
	id: string;
	title: string;
	sourceTaskId: string;
	issue: string;
}

const DEFINITIONS: readonly IssueTaskDefinition[] = [
	{
		id: "issue-dev-pytest-10051",
		title: "caplog.clear 后阶段记录视图失去同步",
		sourceTaskId: "real-pytest-10051",
		issue: `caplog.get_records and caplog.clear conflict

caplog.get_records() becomes decoupled from caplog.records when caplog.clear() is called. During setup both views refer to the same list, but clear() replaces one list. After clear(), get_records("call") is neither cleared nor updated with new records.

Expected behavior: caplog.get_records("call") and caplog.records remain consistent before and after caplog.clear(), while records from other test phases remain available through their phase-specific views.`,
	},
	{
		id: "issue-dev-pytest-7490",
		title: "运行期动态添加 xfail 后结果语义错误",
		sourceTaskId: "real-pytest-7490",
		issue: `Pytest 6: dynamically adding an xfail marker in a running test no longer handles the result like a static xfail marker.

A test may call request.node.add_marker(pytest.mark.xfail(...)) while it is running. In pytest 5.x, a subsequent failure is reported as xfailed. In the affected version it is reported as an ordinary failure. Strict xfail behavior for a passing test must also remain consistent with statically declared markers.`,
	},
	{
		id: "issue-dev-pylint-7080",
		title: "递归扫描忽略 ignore-paths 配置",
		sourceTaskId: "real-pylint-7080",
		issue: `--recursive=y ignores ignore-paths.

When Pylint recursively scans a directory, an anchored ignore-paths expression such as ^src/gen/.*$ is not applied consistently. Files below the generated directory are linted even though the configuration excludes them.

Expected behavior: recursive discovery honors ignore-paths while continuing to discover nearby non-matching Python files.`,
	},
	{
		id: "issue-dev-pylint-6903",
		title: "分数 CPU 配额使 --jobs=0 创建零进程池",
		sourceTaskId: "real-pylint-6903",
		issue: `Running Pylint in a Kubernetes pod with --jobs=0 can fail because automatic CPU detection returns zero.

One observed cgroup-v1 environment reports cpu.cfs_quota_us=-1, cpu.cfs_period_us=100000, and cpu.shares=2. Converting cpu.shares / 1024 to an integer produces zero, and multiprocessing then raises "Number of processes must be at least 1".

Expected behavior: automatic worker selection always returns at least one usable worker and Pylint does not crash in this environment.`,
	},
];

function materialize(definition: IssueTaskDefinition): IssueTask {
	const source = getRealCodeTask(definition.sourceTaskId);
	const prompt = `Resolve the reported issue in the supplied repository.

Issue:
${definition.issue}

Inspect the repository, implement the necessary change, and run relevant validation. You may add new tests when useful, but do not modify or delete existing tests or dependency lock files.

Report the files changed, commands run, observed results, and remaining limitations. Do not claim completion without execution evidence.`;
	return {
		id: definition.id,
		title: definition.title,
		instanceId: source.instanceId,
		repository: source.repository,
		baseCommit: source.baseCommit,
		image: source.image,
		difficulty: source.difficulty,
		split: "development",
		sourceTaskId: source.id,
		issue: definition.issue,
		prompt,
		goldPatchPath: source.goldPatchPath,
		upstreamTestPatchPath: source.upstreamTestPatchPath,
		hiddenTestCommand: source.hiddenTestCommand,
		regressionCommand: source.regressionCommand,
		newTestCommandPrefix: "pytest -q",
		newTestPathMode: "path",
		testPathMode: source.repository === "pytest-dev/pytest" ? "testing-root" : "tests-root",
	};
}

export function getIssueTasks(): IssueTask[] {
	return [...DEFINITIONS.map((definition) => materialize(definition)), ...getEvaluationIssueTasks()];
}

export function getIssueTask(id: string): IssueTask {
	const definition = DEFINITIONS.find((candidate) => candidate.id === id);
	if (definition) return materialize(definition);
	const evaluation = getEvaluationIssueTasks().find((candidate) => candidate.id === id);
	if (!evaluation) throw new Error(`Unknown issue task: ${id}`);
	return structuredClone(evaluation);
}
