import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { IssueNewTestPathMode, IssueTask, IssueTaskSplit, IssueTestPathMode } from "./issue-types.ts";
import type { RealCodeDifficulty } from "./types.ts";

interface EvaluationTaskDefinition {
	id: string;
	title: string;
	instanceId: string;
	repository: string;
	baseCommit: string;
	image: string;
	difficulty: RealCodeDifficulty;
	split: Exclude<IssueTaskSplit, "development" | "regression">;
	hiddenTestCommand: string;
	regressionCommand: string;
	newTestCommandPrefix: string;
	newTestPathMode?: IssueNewTestPathMode;
	testPathMode: IssueTestPathMode;
	toolSmokeArgv: string[];
}

function assetPath(taskId: string, name: "issue.md" | "gold.patch" | "upstream-tests.patch"): string {
	return fileURLToPath(new URL(`../../datasets/issue-agent-v1/${taskId}/${name}`, import.meta.url));
}

const DEFINITIONS: readonly EvaluationTaskDefinition[] = [
	{
		id: "issue-eval-astropy-12907",
		title: "嵌套 CompoundModel 的可分离矩阵错误",
		instanceId: "astropy__astropy-12907",
		repository: "astropy/astropy",
		baseCommit: "d16bfe05a744909de4b27f5875fe0d4ed41ce607",
		image: "logicstar/sweb.eval.x86_64.astropy_1776_astropy-12907:latest",
		difficulty: "composite",
		split: "evaluation-core",
		hiddenTestCommand:
			"pytest -q 'astropy/modeling/tests/test_separable.py::test_separable[compound_model6-result6]' 'astropy/modeling/tests/test_separable.py::test_separable[compound_model9-result9]'",
		regressionCommand: "pytest -q astropy/modeling/tests/test_separable.py",
		newTestCommandPrefix: "pytest -q",
		testPathMode: "tests-segment",
		toolSmokeArgv: ["pytest", "-q", "astropy/modeling/tests/test_separable.py", "--collect-only"],
	},
	{
		id: "issue-eval-django-11141",
		title: "无 __init__.py 的 migrations namespace package 无法加载",
		instanceId: "django__django-11141",
		repository: "django/django",
		baseCommit: "5d9cf79baf07fc4aed7ad1b06990532a65378155",
		image: "logicstar/sweb.eval.x86_64.django_1776_django-11141:latest",
		difficulty: "composite",
		split: "evaluation-core",
		hiddenTestCommand:
			"python tests/runtests.py migrations.test_loader.LoaderTests.test_loading_namespace_package --verbosity 0",
		regressionCommand: "python tests/runtests.py migrations.test_loader.LoaderTests --verbosity 0",
		newTestCommandPrefix: "python tests/runtests.py",
		newTestPathMode: "django-label",
		testPathMode: "tests-root",
		toolSmokeArgv: [
			"python",
			"tests/runtests.py",
			"migrations.test_loader.LoaderTests.test_load",
			"--verbosity",
			"0",
		],
	},
	{
		id: "issue-eval-sklearn-13142",
		title: "GaussianMixture 多次初始化后 fit_predict 与 predict 不一致",
		instanceId: "scikit-learn__scikit-learn-13142",
		repository: "scikit-learn/scikit-learn",
		baseCommit: "1c8668b0a021832386470ddf740d834e02c66f69",
		image: "logicstar/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-13142:latest",
		difficulty: "basic",
		split: "evaluation-core",
		hiddenTestCommand:
			"pytest -q sklearn/mixture/tests/test_bayesian_mixture.py::test_bayesian_mixture_fit_predict_n_init sklearn/mixture/tests/test_gaussian_mixture.py::test_gaussian_mixture_fit_predict_n_init",
		regressionCommand:
			"pytest -q sklearn/mixture/tests/test_bayesian_mixture.py sklearn/mixture/tests/test_gaussian_mixture.py",
		newTestCommandPrefix: "pytest -q",
		testPathMode: "tests-segment",
		toolSmokeArgv: ["pytest", "-q", "sklearn/mixture/tests/test_gaussian_mixture.py", "--collect-only"],
	},
	{
		id: "issue-eval-sphinx-10449",
		title: "autoclass 错误生成构造函数返回类型",
		instanceId: "sphinx-doc__sphinx-10449",
		repository: "sphinx-doc/sphinx",
		baseCommit: "36367765fe780f962bba861bf368a765380bbc68",
		image: "logicstar/sweb.eval.x86_64.sphinx-doc_1776_sphinx-10449:latest",
		difficulty: "basic",
		split: "evaluation-core",
		hiddenTestCommand:
			"pytest -q tests/test_ext_autodoc_configs.py::test_autodoc_typehints_description_with_documented_init",
		regressionCommand: "pytest -q tests/test_ext_autodoc_configs.py",
		newTestCommandPrefix: "pytest -q",
		testPathMode: "tests-root",
		toolSmokeArgv: ["pytest", "-q", "tests/test_ext_autodoc_configs.py", "--collect-only"],
	},
	{
		id: "issue-eval-matplotlib-23412",
		title: "Patch dash linestyle offset 未传递给 renderer",
		instanceId: "matplotlib__matplotlib-23412",
		repository: "matplotlib/matplotlib",
		baseCommit: "f06c2c3abdaf4b90285ce5ca7fedbb8ace715911",
		image: "logicstar/sweb.eval.x86_64.matplotlib_1776_matplotlib-23412:latest",
		difficulty: "composite",
		split: "evaluation-expansion-a",
		hiddenTestCommand: "pytest -q 'lib/matplotlib/tests/test_patches.py::test_dash_offset_patch_draw[png]'",
		regressionCommand: "pytest -q lib/matplotlib/tests/test_patches.py",
		newTestCommandPrefix: "pytest -q",
		testPathMode: "tests-segment",
		toolSmokeArgv: ["pytest", "-q", "lib/matplotlib/tests/test_patches.py", "--collect-only"],
	},
	{
		id: "issue-eval-requests-1142",
		title: "无 body 的 GET 请求仍自动发送 Content-Length",
		instanceId: "psf__requests-1142",
		repository: "psf/requests",
		baseCommit: "22623bd8c265b78b161542663ee980738441c307",
		image: "logicstar/sweb.eval.x86_64.psf_1776_requests-1142:latest",
		difficulty: "basic",
		split: "evaluation-expansion-a",
		hiddenTestCommand: "pytest -q test_requests.py::RequestsTestCase::test_no_content_length",
		regressionCommand:
			"pytest -q test_requests.py::RequestsTestCase::test_basic_building test_requests.py::RequestsTestCase::test_entry_points test_requests.py::RequestsTestCase::test_invalid_url test_requests.py::RequestsTestCase::test_params_are_added_before_fragment test_requests.py::RequestsTestCase::test_path_is_not_double_encoded",
		newTestCommandPrefix: "pytest -q",
		testPathMode: "root-test-files",
		toolSmokeArgv: ["pytest", "-q", "test_requests.py", "--collect-only"],
	},
	{
		id: "issue-eval-xarray-3677",
		title: "Dataset.merge 无法接受 DataArray",
		instanceId: "pydata__xarray-3677",
		repository: "pydata/xarray",
		baseCommit: "ef6e6a7b86f8479b9a1fecf15ad5b88a2326b31e",
		image: "logicstar/sweb.eval.x86_64.pydata_1776_xarray-3677:latest",
		difficulty: "composite",
		split: "evaluation-expansion-a",
		hiddenTestCommand: "pytest -q xarray/tests/test_merge.py::TestMergeMethod::test_merge_dataarray",
		regressionCommand: "pytest -q xarray/tests/test_merge.py",
		newTestCommandPrefix: "pytest -q",
		testPathMode: "tests-segment",
		toolSmokeArgv: ["pytest", "-q", "xarray/tests/test_merge.py", "--collect-only"],
	},
	{
		id: "issue-eval-pytest-8399",
		title: "unittest 自动 fixture 不再保持私有命名",
		instanceId: "pytest-dev__pytest-8399",
		repository: "pytest-dev/pytest",
		baseCommit: "6e7dc8bac831cd8cf7a53b08efa366bd84f0c0fe",
		image: "logicstar/sweb.eval.x86_64.pytest-dev_1776_pytest-8399:latest",
		difficulty: "composite",
		split: "evaluation-expansion-a",
		hiddenTestCommand: "pytest -q testing/test_unittest.py::test_fixtures_setup_setUpClass_issue8394",
		regressionCommand: "pytest -q testing/test_unittest.py testing/test_nose.py",
		newTestCommandPrefix: "pytest -q",
		testPathMode: "testing-root",
		toolSmokeArgv: ["pytest", "-q", "testing/test_unittest.py", "--collect-only"],
	},
	{
		id: "issue-eval-astropy-13453",
		title: "HTML table writer 未传播列格式配置",
		instanceId: "astropy__astropy-13453",
		repository: "astropy/astropy",
		baseCommit: "19cc80471739bcb67b7e8099246b391c355023ee",
		image: "logicstar/sweb.eval.x86_64.astropy_1776_astropy-13453:latest",
		difficulty: "composite",
		split: "evaluation-expansion-b",
		hiddenTestCommand: "pytest -q astropy/io/ascii/tests/test_html.py::test_write_table_formatted_columns",
		regressionCommand: "pytest -q astropy/io/ascii/tests/test_html.py",
		newTestCommandPrefix: "pytest -q",
		testPathMode: "tests-segment",
		toolSmokeArgv: ["pytest", "-q", "astropy/io/ascii/tests/test_html.py", "--collect-only"],
	},
	{
		id: "issue-eval-django-11133",
		title: "HttpResponse 错误序列化 memoryview 内容",
		instanceId: "django__django-11133",
		repository: "django/django",
		baseCommit: "879cc3da6249e920b8d54518a0ae06de835d7373",
		image: "logicstar/sweb.eval.x86_64.django_1776_django-11133:latest",
		difficulty: "basic",
		split: "evaluation-expansion-b",
		hiddenTestCommand:
			"python tests/runtests.py httpwrappers.tests.HttpResponseTests.test_memoryview_content --verbosity 0",
		regressionCommand: "python tests/runtests.py httpwrappers --verbosity 0",
		newTestCommandPrefix: "python tests/runtests.py",
		newTestPathMode: "django-label",
		testPathMode: "tests-root",
		toolSmokeArgv: [
			"python",
			"tests/runtests.py",
			"httpwrappers.tests.HttpResponseTests.test_non_string_content",
			"--verbosity",
			"0",
		],
	},
	{
		id: "issue-eval-sklearn-13328",
		title: "HuberRegressor 无法处理布尔特征矩阵",
		instanceId: "scikit-learn__scikit-learn-13328",
		repository: "scikit-learn/scikit-learn",
		baseCommit: "37b0e66c871e8fb032a9c7086b2a1d5419838154",
		image: "logicstar/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-13328:latest",
		difficulty: "basic",
		split: "evaluation-expansion-b",
		hiddenTestCommand: "pytest -q sklearn/linear_model/tests/test_huber.py::test_huber_bool",
		regressionCommand: "pytest -q sklearn/linear_model/tests/test_huber.py",
		newTestCommandPrefix: "pytest -q",
		testPathMode: "tests-segment",
		toolSmokeArgv: ["pytest", "-q", "sklearn/linear_model/tests/test_huber.py", "--collect-only"],
	},
	{
		id: "issue-eval-sympy-11618",
		title: "不同维度 Point 的距离计算忽略额外坐标",
		instanceId: "sympy__sympy-11618",
		repository: "sympy/sympy",
		baseCommit: "360290c4c401e386db60723ddb0109ed499c9f6e",
		image: "logicstar/sweb.eval.x86_64.sympy_1776_sympy-11618:latest",
		difficulty: "composite",
		split: "evaluation-expansion-b",
		hiddenTestCommand:
			"python bin/test sympy/geometry/tests/test_point.py -k test_issue_11617 --no-colors --no-subprocess",
		regressionCommand: "python bin/test sympy/geometry/tests/test_point.py --no-colors --no-subprocess",
		newTestCommandPrefix: "python bin/test --no-colors --no-subprocess",
		testPathMode: "tests-segment",
		toolSmokeArgv: [
			"python",
			"bin/test",
			"sympy/geometry/tests/test_point.py",
			"-k",
			"test_Point2D",
			"--no-colors",
			"--no-subprocess",
		],
	},
];

function materialize(definition: EvaluationTaskDefinition): IssueTask {
	const issue = readFileSync(assetPath(definition.id, "issue.md"), "utf8").trim();
	const prompt = `Resolve the reported issue in the supplied repository.

Issue:
${issue}

Inspect the repository, implement the necessary change, and run relevant validation. You may add new tests when useful, but do not modify or delete existing tests or dependency lock files.

Report the files changed, commands run, observed results, and remaining limitations. Do not claim completion without execution evidence.`;
	return {
		...definition,
		sourceTaskId: definition.instanceId,
		issue,
		prompt,
		goldPatchPath: assetPath(definition.id, "gold.patch"),
		upstreamTestPatchPath: assetPath(definition.id, "upstream-tests.patch"),
		newTestPathMode: definition.newTestPathMode ?? "path",
	};
}

export function getEvaluationIssueTasks(): IssueTask[] {
	return DEFINITIONS.map((definition) => materialize(definition));
}
