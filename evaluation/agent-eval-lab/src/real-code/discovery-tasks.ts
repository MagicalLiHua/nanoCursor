import { getRealCodeTask } from "./tasks.ts";
import type { RealCodeTask } from "./types.ts";

interface DiscoveryTaskDefinition {
	id: string;
	title: string;
	sourceTaskId: string;
	prompt: string;
}

const DEFINITIONS: readonly DiscoveryTaskDefinition[] = [
	{
		id: "discover-sphinx-10323",
		title: "探索 literalinclude 内容转换与选项组合",
		sourceTaskId: "real-sphinx-10323",
		prompt: `Act as a software testing engineer responsible for Sphinx's literalinclude feature.

Feature brief:
- LiteralIncludeReader selects content from a source file and applies documented transformations such as line selection, indentation adjustment, tab expansion, prepended text, and appended text.
- Each option should work alone, and compatible options should compose without corrupting content or producing misleading output.
- Tests should reflect observable behavior through public or established test interfaces.

Independently inspect the implementation, documentation, and nearby tests. Build a small risk list, choose high-value boundaries or option interactions, and create focused tests under tests/agent_generated/. Run the tests and investigate any reproducible assertion failure. Preserve a failing test only when it expresses a defensible feature contract; do not manufacture a failure or assume every surprising result is a product defect.

Do not modify Sphinx product source. Finish with an evidence-based test report that separates confirmed findings from unproven hypotheses and names any remaining coverage limitation.`,
	},
	{
		id: "discover-django-16454",
		title: "探索管理命令参数解析器与子解析器",
		sourceTaskId: "real-django-16454",
		prompt: `Act as a software testing engineer responsible for Django management-command argument parsing.

Feature brief:
- Management commands create parsers for command-line and programmatic use.
- Commands may add typed arguments, nested subparsers, and explicitly selected parser classes.
- Valid inputs should parse consistently, while invalid inputs should follow the error-handling behavior of the parser and invocation mode in use.

Independently inspect the implementation, documentation, and nearby tests. Build a small risk list, choose high-value parser combinations and error boundaries, and create focused tests under tests/agent_generated/. Run the tests and investigate any reproducible assertion failure. Preserve a failing test only when it expresses a defensible public behavior; do not manufacture a failure or assume every exception is a product defect.

Do not modify Django product source. Finish with an evidence-based test report that separates confirmed findings from unproven hypotheses and names any remaining coverage limitation.`,
	},
];

function materialize(definition: DiscoveryTaskDefinition): RealCodeTask {
	const source = getRealCodeTask(definition.sourceTaskId);
	return {
		...source,
		id: definition.id,
		title: definition.title,
		taskType: "test-generation",
		split: "development",
		prompt: definition.prompt,
		mode: "discovery",
		sourceTaskId: definition.sourceTaskId,
	};
}

export function getRealCodeDiscoveryTasks(): RealCodeTask[] {
	return DEFINITIONS.map((definition) => materialize(definition));
}

export function getRealCodeDiscoveryTask(id: string): RealCodeTask {
	const definition = DEFINITIONS.find((candidate) => candidate.id === id);
	if (!definition) throw new Error(`Unknown real-code discovery task: ${id}`);
	return materialize(definition);
}
