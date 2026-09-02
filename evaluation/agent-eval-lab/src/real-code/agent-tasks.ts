import { getRealCodeTask } from "./tasks.ts";
import type { RealCodeTask } from "./types.ts";

interface AgentTaskDefinition {
	id: string;
	title: string;
	sourceTaskId: string;
	prompt: string;
}

const DEFINITIONS: readonly AgentTaskDefinition[] = [
	{
		id: "agent-dev-pytest-caplog",
		title: "为 caplog.clear 阶段记录同步补充回归覆盖",
		sourceTaskId: "real-pytest-10051",
		prompt: `Objective: add focused regression coverage for pytest's caplog.clear() behavior.

Requirements:
- caplog.get_records("call") represents records captured during the call phase.
- caplog.clear() clears the current call-phase records without erasing records from earlier phases.
- records emitted after clear() are visible through both caplog.records and caplog.get_records("call").

Deliverables:
- Add focused tests under testing/agent_generated/ without modifying pytest product source or existing tests.
- Cover state before clear(), immediately after clear(), after another record is emitted, and phase isolation.
- Run the generated tests and submit a concise report naming files, commands, observed results, and remaining limitations.

Use repository code, documentation, and nearby tests as implementation references. A focused assertion failure on the supplied repository is acceptable evidence of a regression; collection, import, syntax, timeout, or unrelated failures are not.`,
	},
	{
		id: "agent-dev-pytest-dynamic-xfail",
		title: "为运行期动态 xfail 结果语义补充回归覆盖",
		sourceTaskId: "real-pytest-7490",
		prompt: `Objective: add regression coverage for xfail markers added while a pytest test is already running.

Requirements:
- A failing test that dynamically adds a non-strict xfail marker through request.node.add_marker(...) is reported as xfailed.
- A passing test that dynamically adds a strict xfail marker is reported as a strict XPASS failure.
- Assertions must use observable nested-run outcomes rather than version checks or source inspection.

Deliverables:
- Add focused tests under testing/agent_generated/ without modifying pytest product source or existing tests.
- Cover both branches and explicitly distinguish their outcomes.
- Run the generated tests and submit a concise report naming files, commands, observed results, and remaining limitations.

Use existing skipping tests and pytest's established test-running fixtures as references. A target-behavior assertion failure is useful evidence; unrelated collection or fixture errors are not.`,
	},
	{
		id: "agent-dev-pylint-ignore-paths",
		title: "为递归扫描 ignore-paths 路径匹配补充回归覆盖",
		sourceTaskId: "real-pylint-7080",
		prompt: `Objective: add regression coverage for Pylint recursive discovery with anchored --ignore-paths expressions.

Requirements:
- Running against the current directory with recursive discovery enabled honors an anchored ignore path written relative to that directory.
- A matching nested directory is excluded.
- A nearby non-matching Python path is still discovered and linted.

Deliverables:
- Add focused tests under tests/agent_generated/ without modifying Pylint product source or existing tests.
- Exercise the public runner from a temporary directory and include a non-regression case that rules out blanket ignoring.
- Run the generated tests and submit a concise report naming files, commands, observed results, and remaining limitations.

Use existing recursive-discovery tests as references. Preserve only assertions grounded in the requirements; import, collection, timeout, and fixture failures are not valid completion evidence.`,
	},
	{
		id: "agent-dev-pylint-cgroup-jobs",
		title: "为分数 CPU 配额下的自动并行度补充回归覆盖",
		sourceTaskId: "real-pylint-6903",
		prompt: `Objective: add regression coverage for Pylint automatic parallelism selection under Linux cgroup-v1 limits.

Requirements:
- --jobs=0 derives a usable worker count from the environment.
- An unlimited quota combined with CPU shares below one full core must still yield at least one worker.
- The test must not depend on host timing, network access, or the host's actual cgroup files.

Deliverables:
- Add focused tests under tests/agent_generated/ without modifying Pylint product source or existing tests.
- Simulate the relevant cgroup files, verify the target worker-count behavior, and include a nearby normal CPU-count case.
- Run the generated tests and submit a concise report naming files, commands, observed results, and remaining limitations.

Use nearby runner tests and their mocking conventions as references. Prefer the smallest public or established interface that proves the requirement; unrelated multiprocessing or capture failures are not valid evidence.`,
	},
];

function materialize(definition: AgentTaskDefinition): RealCodeTask {
	const source = getRealCodeTask(definition.sourceTaskId);
	return {
		...source,
		id: definition.id,
		title: definition.title,
		split: "development",
		prompt: definition.prompt,
		mode: "agent-task",
		sourceTaskId: definition.sourceTaskId,
	};
}

export function getAgentTasks(): RealCodeTask[] {
	return DEFINITIONS.map((definition) => materialize(definition));
}

export function getAgentTask(id: string): RealCodeTask {
	const definition = DEFINITIONS.find((candidate) => candidate.id === id);
	if (!definition) throw new Error(`Unknown agent task: ${id}`);
	return materialize(definition);
}
