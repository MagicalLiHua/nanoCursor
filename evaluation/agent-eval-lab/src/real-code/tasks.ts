import { fileURLToPath } from "node:url";
import type { RealCodeTask } from "./types.ts";

function assetPath(taskId: string, name: "gold.patch" | "upstream-tests.patch"): string {
	return fileURLToPath(new URL(`../../datasets/real-python-v1/${taskId}/${name}`, import.meta.url));
}

const TASKS: readonly RealCodeTask[] = [
	{
		id: "real-pytest-10051",
		title: "caplog.clear 后阶段日志记录失去同步",
		instanceId: "pytest-dev__pytest-10051",
		repository: "pytest-dev/pytest",
		baseCommit: "aa55975c7d3f6c9f6d7f68accc41bb7cadf0eb9a",
		image: "logicstar/sweb.eval.x86_64.pytest-dev_1776_pytest-10051:latest",
		difficulty: "composite",
		taskType: "test-generation",
		split: "development",
		prompt: `You are testing a reported pytest regression in caplog.

Observed contract:
- caplog.get_records("call") represents the records captured during the call phase.
- caplog.clear() should clear the current call-phase records without erasing records from earlier phases.
- log records emitted after clear() must become visible through both caplog.records and caplog.get_records("call").

In the supplied buggy repository, these views can become detached after clear(). Create focused regression tests under testing/agent_generated/ that expose the defect and cover the state before clear, immediately after clear, and after a new log record is emitted. Include a nearby non-regression assertion for phase isolation.

Do not modify pytest product source. Inspect the repository, write tests with test_write, and run them with test_run. A meaningful failure on this buggy version is the expected reproduction result. Finish with a short evidence-based explanation of what failed and why the test is specific to the reported behavior.`,
		goldPatchPath: assetPath("real-pytest-10051", "gold.patch"),
		upstreamTestPatchPath: assetPath("real-pytest-10051", "upstream-tests.patch"),
		generatedTestRoot: "testing/agent_generated",
		agentTestCommand: "pytest -q testing/agent_generated",
		hiddenTestCommand: "pytest -q testing/logging/test_fixture.py::test_clear_for_call_stage",
		regressionCommand: "pytest -q testing/logging/test_fixture.py",
	},
	{
		id: "real-pytest-7490",
		title: "运行期间动态添加 xfail 后结果语义错误",
		instanceId: "pytest-dev__pytest-7490",
		repository: "pytest-dev/pytest",
		baseCommit: "7f7a36478abe7dd1fa993b115d22606aa0e35e88",
		image: "logicstar/sweb.eval.x86_64.pytest-dev_1776_pytest-7490:latest",
		difficulty: "hard",
		taskType: "patch-verification",
		split: "development",
		prompt: `You are testing a pytest regression involving xfail markers added while a test is already running.

Expected behavior:
- If a test dynamically adds a non-strict xfail marker through request.node.add_marker(...) and then fails, the nested run should report one xfailed test rather than an ordinary failure.
- If a passing test dynamically adds a strict xfail marker, the nested run should report a strict XPASS as a failure.

Create focused regression tests under testing/agent_generated/. Exercise both branches through pytest's own test-running facilities and assert the observable run outcomes, not private source text or version numbers. Add at least one assertion that distinguishes the two branches.

Do not modify pytest product source. Inspect existing skipping tests for conventions, write tests with test_write, and run them with test_run. A meaningful failure on this buggy version is the expected reproduction result. Finish with a concise explanation grounded in the run output.`,
		goldPatchPath: assetPath("real-pytest-7490", "gold.patch"),
		upstreamTestPatchPath: assetPath("real-pytest-7490", "upstream-tests.patch"),
		generatedTestRoot: "testing/agent_generated",
		agentTestCommand: "pytest -q testing/agent_generated",
		hiddenTestCommand:
			"pytest -q testing/test_skipping.py::TestXFail::test_dynamic_xfail_set_during_runtest_failed testing/test_skipping.py::TestXFail::test_dynamic_xfail_set_during_runtest_passed_strict",
		regressionCommand: "pytest -q testing/test_skipping.py",
	},
	{
		id: "real-pylint-7080",
		title: "递归扫描时 ignore-paths 未按规范化路径匹配",
		instanceId: "pylint-dev__pylint-7080",
		repository: "pylint-dev/pylint",
		baseCommit: "3c5eca2ded3dd2b59ebaf23eb289453b5d2930f0",
		image: "logicstar/sweb.eval.x86_64.pylint-dev_1776_pylint-7080:latest",
		difficulty: "composite",
		taskType: "test-generation",
		split: "development",
		prompt: `You are testing a reported Pylint regression in recursive directory discovery.

Observed contract:
- A user can run Pylint against the current directory with recursive discovery enabled.
- An anchored --ignore-paths expression written relative to that directory must exclude matching nested paths.
- Nearby non-matching Python files must still be discovered and linted normally.

In the supplied buggy repository, path spelling during recursive discovery can prevent the anchored ignore expression from matching. Create focused regression tests under tests/agent_generated/ that execute the public Pylint runner from a temporary directory. Demonstrate that a nested ignored directory is excluded while a sibling non-ignored path remains covered. Include a nearby non-regression case that distinguishes path-based ignore behavior from a blanket ignore.

Do not modify Pylint product source. Inspect existing recursive-discovery tests for conventions, write tests with test_write, and run them with test_run. A meaningful failure on this buggy version is the expected reproduction result. Finish with a concise explanation grounded in the observed runner behavior.`,
		goldPatchPath: assetPath("real-pylint-7080", "gold.patch"),
		upstreamTestPatchPath: assetPath("real-pylint-7080", "upstream-tests.patch"),
		generatedTestRoot: "tests/agent_generated",
		agentTestCommand: "pytest -q tests/agent_generated",
		hiddenTestCommand: "pytest -q tests/test_self.py::TestRunTC::test_ignore_path_recursive_current_dir",
		regressionCommand:
			"pytest -q tests/test_self.py::TestRunTC::test_ignore_path_recursive tests/test_self.py::TestRunTC::test_ignore_pattern_recursive tests/test_self.py::TestRunTC::test_recursive_current_dir tests/test_self.py::TestRunTC::test_regression_recursive_current_dir",
	},
	{
		id: "real-pylint-6903",
		title: "自动并行度在分数 CPU 配额下产生零进程",
		instanceId: "pylint-dev__pylint-6903",
		repository: "pylint-dev/pylint",
		baseCommit: "ca80f03a43bc39e4cc2c67dc99817b3c9f13b8a6",
		image: "logicstar/sweb.eval.x86_64.pylint-dev_1776_pylint-6903:latest",
		difficulty: "hard",
		taskType: "test-generation",
		split: "development",
		prompt: `You are testing a reported Pylint regression in automatic parallelism selection.

Observed contract:
- --jobs=0 asks Pylint to derive a usable worker count from the current environment.
- A Linux cgroup may report an unlimited quota together with CPU shares below one full core.
- Pylint must still choose at least one worker and complete instead of attempting to create a zero-process pool.

Create focused regression tests under tests/agent_generated/. Simulate the relevant cgroup-v1 files without depending on the host machine, then exercise the public Pylint runner with --jobs=0 and assert the observable completion status. Add a nearby non-regression assertion for a normal CPU result. Keep the test independent of network access and timing.

Do not modify Pylint product source. Inspect the runner implementation and nearby tests for mocking conventions, write tests with test_write, and run them with test_run. A meaningful failure on this buggy version is the expected reproduction result. Finish with a concise explanation grounded in the exception or exit status you observed.`,
		goldPatchPath: assetPath("real-pylint-6903", "gold.patch"),
		upstreamTestPatchPath: assetPath("real-pylint-6903", "upstream-tests.patch"),
		generatedTestRoot: "tests/agent_generated",
		agentTestCommand: "pytest -q tests/agent_generated",
		hiddenTestCommand:
			"pytest -q tests/test_pylint_runners.py::test_pylint_run_jobs_equal_zero_dont_crash_with_cpu_fraction",
		regressionCommand: "pytest -q tests/test_pylint_runners.py",
	},
	{
		id: "real-sphinx-10323",
		title: "literalinclude 组合选项破坏补入文本的缩进",
		instanceId: "sphinx-doc__sphinx-10323",
		repository: "sphinx-doc/sphinx",
		baseCommit: "31eba1a76dd485dc633cae48227b46879eda5df4",
		image: "logicstar/sweb.eval.x86_64.sphinx-doc_1776_sphinx-10323:latest",
		difficulty: "composite",
		taskType: "test-generation",
		split: "development",
		prompt: `You are testing a Sphinx regression in the literalinclude directive.

Observed contract:
- literalinclude may select a range of lines and remove indentation from the included file.
- prepend and append add synthetic boundary lines around that selected content.
- Dedenting the included file must not strip meaningful text or indentation from the synthetic boundary lines.

Create focused regression tests under tests/agent_generated/ using Sphinx's LiteralIncludeReader and existing fixtures. Combine line selection, dedent, prepend, and append, and assert the exact observable content. Include a nearby case without synthetic lines or without dedent so the test distinguishes the option interaction from basic file reading.

Do not modify Sphinx product source. Inspect nearby directive tests for conventions, write tests with test_write, and run them with test_run. A meaningful assertion failure on this buggy version is the expected reproduction result. Finish with a concise explanation grounded in the observed output.`,
		goldPatchPath: assetPath("real-sphinx-10323", "gold.patch"),
		upstreamTestPatchPath: assetPath("real-sphinx-10323", "upstream-tests.patch"),
		generatedTestRoot: "tests/agent_generated",
		agentTestCommand: "pytest -q tests/agent_generated",
		hiddenTestCommand:
			"pytest -q tests/test_directive_code.py::test_LiteralIncludeReader_dedent_and_append_and_prepend",
		regressionCommand: "pytest -q tests/test_directive_code.py",
	},
	{
		id: "real-sphinx-11445",
		title: "rst_prolog 错把域角色标题识别为文档元数据",
		instanceId: "sphinx-doc__sphinx-11445",
		repository: "sphinx-doc/sphinx",
		baseCommit: "71db08c05197545944949d5aa76cd340e7143627",
		image: "logicstar/sweb.eval.x86_64.sphinx-doc_1776_sphinx-11445:latest",
		difficulty: "composite",
		taskType: "test-generation",
		split: "final-test",
		prompt: `You are testing a Sphinx regression in rst_prolog insertion.

Observed contract:
- Document metadata fields may appear before the document body and the configured prolog is inserted after them.
- A domain role such as :mod: used as the first section title is body content, not a metadata field.
- Adding a prolog must preserve that title and its source ordering, with or without a trailing newline in the prolog.

Create focused regression tests under tests/agent_generated/ using prepend_prolog and StringList. Cover a domain-role title at the start of the document for both trailing-newline forms, and include a nearby case with genuine metadata fields. Assert the resulting content and source positions rather than implementation names.

Do not modify Sphinx product source. Inspect nearby rst utility tests for conventions, write tests with test_write, and run them with test_run. A meaningful assertion failure on this buggy version is the expected reproduction result. Finish with a concise explanation grounded in the observed content.`,
		goldPatchPath: assetPath("real-sphinx-11445", "gold.patch"),
		upstreamTestPatchPath: assetPath("real-sphinx-11445", "upstream-tests.patch"),
		generatedTestRoot: "tests/agent_generated",
		agentTestCommand: "pytest -q tests/agent_generated",
		hiddenTestCommand:
			"pytest -q tests/test_util_rst.py::test_prepend_prolog_with_roles_in_sections_with_newline tests/test_util_rst.py::test_prepend_prolog_with_roles_in_sections_without_newline",
		regressionCommand: "pytest -q tests/test_util_rst.py",
	},
	{
		id: "real-django-16454",
		title: "管理命令子解析器丢失 CLI 错误格式",
		instanceId: "django__django-16454",
		repository: "django/django",
		baseCommit: "1250483ebf73f7a82ff820b94092c63ce4238264",
		image: "logicstar/sweb.eval.x86_64.django_1776_django-16454:latest",
		difficulty: "composite",
		taskType: "patch-verification",
		split: "development",
		prompt: `You are testing a Django management-command regression involving argument subparsers.

Observed contract:
- A management command created for command-line use reports invalid arguments as a two-line usage/error message rather than a Python traceback.
- A subparser created from Django's command parser must retain that command-line error behavior.
- An explicitly supplied standard argparse parser class must retain normal argparse behavior.

Create focused regression tests under tests/agent_generated/. Exercise nested parsers with an invalid typed argument and assert the observable exception or stderr behavior for both Django and explicitly standard parser classes. Include a valid-argument case so the test does not merely require every nested parse to fail.

Do not modify Django product source. Inspect the management parser and nearby user-command tests for conventions, write tests with test_write, and run them with test_run. A meaningful assertion failure on this buggy version is the expected reproduction result. Finish with a concise explanation grounded in the observed error behavior.`,
		goldPatchPath: assetPath("real-django-16454", "gold.patch"),
		upstreamTestPatchPath: assetPath("real-django-16454", "upstream-tests.patch"),
		generatedTestRoot: "tests/agent_generated",
		agentTestCommand: "python tests/runtests.py agent_generated",
		hiddenTestCommand: "python tests/runtests.py user_commands.tests.CommandRunTests.test_subparser_error_formatting",
		regressionCommand:
			"python tests/runtests.py user_commands.tests.CommandTests.test_subparser user_commands.tests.CommandTests.test_subparser_invalid_option",
	},
	{
		id: "real-django-14580",
		title: "迁移序列化遗漏 models 导入导致生成代码无效",
		instanceId: "django__django-14580",
		repository: "django/django",
		baseCommit: "36fa071d6ebd18a61c4d7f1b5c9d17106134bd44",
		image: "logicstar/sweb.eval.x86_64.django_1776_django-14580:latest",
		difficulty: "composite",
		taskType: "test-generation",
		split: "final-test",
		prompt: `You are testing a Django migration-writer regression involving serialized model base classes.

Observed contract:
- MigrationWriter serialization returns both a Python expression and every import required to evaluate it.
- django.db.models.Model may appear as a generated model base alongside application-defined bases.
- The complete generated migration source must be valid Python and evaluable without an undefined models name.

Create focused regression tests under tests/agent_generated/. Exercise serialization of models.Model, assert the expression/import contract, and validate that a representative generated snippet can execute with only the returned imports. Include a nearby type serialization case that does not require the models import.

Do not modify Django product source. Inspect migration writer tests and serializer conventions, write tests with test_write, and run them with test_run. A meaningful assertion failure on this buggy version is the expected reproduction result. Finish with a concise explanation grounded in the serialized output.`,
		goldPatchPath: assetPath("real-django-14580", "gold.patch"),
		upstreamTestPatchPath: assetPath("real-django-14580", "upstream-tests.patch"),
		generatedTestRoot: "tests/agent_generated",
		agentTestCommand: "python tests/runtests.py agent_generated",
		hiddenTestCommand: "python tests/runtests.py migrations.test_writer.WriterTests.test_serialize_type_model",
		regressionCommand: "python tests/runtests.py migrations.test_writer",
	},
];

export function getRealCodeTasks(): RealCodeTask[] {
	return TASKS.map((task) => structuredClone(task));
}

export function getRealCodeTask(id: string): RealCodeTask {
	const task = TASKS.find((candidate) => candidate.id === id);
	if (!task) throw new Error(`Unknown real-code task: ${id}`);
	return structuredClone(task);
}
