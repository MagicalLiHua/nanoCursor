import type { EvalResult } from "../types.ts";

export interface SuiteSummary {
	total: number;
	passed: number;
	passRate: number;
	uniqueTasks: number;
	stableTasks: number;
	taskStabilityRate: number;
	averageRequiredToolRecall: number;
	averageToolPrecision: number;
	averagePlanCompletionRate: number;
	policyBlocks: number;
	toolErrors: number;
	recoveredTasks: number;
	duplicateSideEffects: number;
	totalTokens: number;
	averageDurationMs: number;
	layeredPasses: number;
	layeredFailures: number;
	layeredReviews: number;
}

function average(values: number[]): number {
	return values.length === 0 ? 0 : values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function summarize(results: EvalResult[]): SuiteSummary {
	const passed = results.filter((result) => result.passed).length;
	const byTask = new Map<string, EvalResult[]>();
	for (const result of results) byTask.set(result.taskId, [...(byTask.get(result.taskId) ?? []), result]);
	const stableTasks = [...byTask.values()].filter((trials) => trials.every((result) => result.passed)).length;
	return {
		total: results.length,
		passed,
		passRate: results.length === 0 ? 0 : passed / results.length,
		uniqueTasks: byTask.size,
		stableTasks,
		taskStabilityRate: byTask.size === 0 ? 0 : stableTasks / byTask.size,
		averageRequiredToolRecall: average(results.map((result) => result.metrics.requiredToolRecall)),
		averageToolPrecision: average(results.map((result) => result.metrics.toolPrecision)),
		averagePlanCompletionRate: average(results.map((result) => result.metrics.planCompletionRate)),
		policyBlocks: results.reduce((sum, result) => sum + result.metrics.policyBlocks, 0),
		toolErrors: results.reduce((sum, result) => sum + result.metrics.toolErrors, 0),
		recoveredTasks: results.filter((result) => result.metrics.recoveredAfterError).length,
		duplicateSideEffects: results.reduce((sum, result) => sum + result.metrics.duplicateSideEffects, 0),
		totalTokens: results.reduce((sum, result) => sum + result.metrics.totalTokens, 0),
		averageDurationMs: average(results.map((result) => result.metrics.durationMs)),
		layeredPasses: results.filter((result) => result.layeredDecision?.status === "PASS").length,
		layeredFailures: results.filter((result) => result.layeredDecision?.status === "FAIL").length,
		layeredReviews: results.filter((result) => result.layeredDecision?.status === "REVIEW").length,
	};
}

export function renderMarkdownReport(results: EvalResult[]): string {
	const summary = summarize(results);
	const models = [...new Set(results.map((result) => result.model))].join(", ") || "-";
	const policies = [...new Set(results.map((result) => result.policyProfile))].join(", ") || "-";
	const rows = results.map(
		(result) =>
			`| ${result.taskId} | ${result.trialIndex} | ${result.passed ? "PASS" : "FAIL"} | ${result.layeredDecision?.status ?? "-"} | ${result.metrics.toolCalls} | ${result.metrics.toolErrors} | ${result.metrics.totalTokens} | ${result.failures.join("; ") || result.layeredDecision?.review_reasons.join("; ") || "-"} |`,
	);
	const layeredTotal = summary.layeredPasses + summary.layeredFailures + summary.layeredReviews;
	return [
		"# AgentEval 报告",
		"",
		`- 模型：${models}`,
		`- Policy：${policies}`,
		`- Trial：${summary.total}`,
		`- 通过 Trial：${summary.passed}`,
		`- 通过率：${(summary.passRate * 100).toFixed(1)}%`,
		`- 稳定通过任务：${summary.stableTasks}/${summary.uniqueTasks}（${(summary.taskStabilityRate * 100).toFixed(1)}%）`,
		...(layeredTotal > 0
			? [
					`- 分层验收：PASS ${summary.layeredPasses} / FAIL ${summary.layeredFailures} / REVIEW ${summary.layeredReviews}`,
				]
			: []),
		`- 必要工具召回率：${(summary.averageRequiredToolRecall * 100).toFixed(1)}%`,
		`- 工具精确率：${(summary.averageToolPrecision * 100).toFixed(1)}%`,
		`- 计划完成率：${(summary.averagePlanCompletionRate * 100).toFixed(1)}%`,
		`- 策略拦截：${summary.policyBlocks}`,
		`- 工具错误：${summary.toolErrors}`,
		`- 错误后恢复任务：${summary.recoveredTasks}`,
		`- 重复副作用：${summary.duplicateSideEffects}`,
		`- 总 Token：${summary.totalTokens}`,
		`- 平均耗时：${summary.averageDurationMs.toFixed(0)} ms`,
		"",
		"| 任务 | Trial | 硬验收 | 分层结果 | 工具调用 | 工具错误 | Token | 失败/复核原因 |",
		"|---|---:|---:|---:|---:|---:|---:|---|",
		...rows,
		"",
	].join("\n");
}
