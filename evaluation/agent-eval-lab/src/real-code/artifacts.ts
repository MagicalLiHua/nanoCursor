import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import type { RealCodeEvalResult, RealCodePreflightResult } from "./types.ts";

export async function writeRealCodeArtifacts(
	results: RealCodeEvalResult[] | RealCodePreflightResult[],
	path: string,
): Promise<string> {
	const resolved = resolve(path);
	await mkdir(dirname(resolved), { recursive: true });
	await writeFile(resolved, `${JSON.stringify(results, null, 2)}\n`, "utf8");
	return resolved;
}

function runSummary(exitCode: number, durationMs: number): string {
	return `exit=${exitCode}, ${(durationMs / 1000).toFixed(2)}s`;
}

export function renderRealCodeReport(results: RealCodeEvalResult[]): string {
	const lines = ["# Real Python Code Evaluation", ""];
	for (const result of results) {
		lines.push(`## ${result.taskId} / trial ${result.trialIndex}: ${result.passed ? "PASS" : "FAIL"}`);
		lines.push("");
		lines.push(`- Evaluation mode: ${result.evaluationMode}`);
		lines.push(`- Run ID: ${result.runId}`);
		if (result.sourceTaskId) lines.push(`- Hidden source task: ${result.sourceTaskId}`);
		if (result.outcomeStatus) lines.push(`- Outcome: ${result.outcomeStatus}`);
		lines.push(`- Termination: ${result.terminationReason}`);
		lines.push(
			`- Budget: ${result.budget.turnsUsed}/${result.budget.maxTurns} turns${result.budget.maxWallTimeMs ? `, wall limit ${(result.budget.maxWallTimeMs / 1000).toFixed(0)}s` : ""}`,
		);
		lines.push(`- Model: ${result.model}`);
		lines.push(`- Generated tests: ${result.grade.generatedFiles.join(", ") || "none"}`);
		for (const test of result.grade.generatedTests) {
			lines.push(`- Test artifact: ${test.path} (${test.bytes} bytes, sha256=${test.sha256})`);
		}
		lines.push(`- Buggy: ${runSummary(result.grade.buggyRun.exitCode, result.grade.buggyRun.durationMs)}`);
		lines.push(`- Fixed: ${runSummary(result.grade.fixedRun.exitCode, result.grade.fixedRun.durationMs)}`);
		lines.push(
			`- Regression: ${runSummary(result.grade.regressionRun.exitCode, result.grade.regressionRun.durationMs)}`,
		);
		for (const check of result.grade.checks) {
			lines.push(`- ${check.passed ? "PASS" : "FAIL"} ${check.id}: ${check.message}`);
		}
		if (result.runError) lines.push(`- Agent runtime error: ${result.runError}`);
		lines.push("");
	}
	return lines.join("\n");
}

export function renderRealCodePreflight(result: RealCodePreflightResult): string {
	return [
		`# ${result.taskId} preflight: ${result.passed ? "PASS" : "FAIL"}`,
		"",
		`- Image: ${result.image}`,
		`- Base commit: ${result.baseCommit}`,
		`- Buggy hidden test: ${runSummary(result.buggyHiddenRun.exitCode, result.buggyHiddenRun.durationMs)}`,
		`- Fixed hidden test: ${runSummary(result.fixedHiddenRun.exitCode, result.fixedHiddenRun.durationMs)}`,
		`- Regression suite: ${runSummary(result.regressionRun.exitCode, result.regressionRun.durationMs)}`,
	].join("\n");
}
