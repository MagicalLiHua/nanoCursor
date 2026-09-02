import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import type { IssueFrozenManifest } from "./issue-manifest.ts";
import type { IssueEvalResult, IssuePreflightResult, IssueToolSmokeResult } from "./issue-types.ts";

export async function writeIssueArtifacts(
	results: IssueEvalResult[] | IssuePreflightResult[] | IssueToolSmokeResult[],
	path: string,
): Promise<string> {
	const resolved = resolve(path);
	await mkdir(dirname(resolved), { recursive: true });
	await writeFile(resolved, `${JSON.stringify(results, null, 2)}\n`, "utf8");
	return resolved;
}

export function renderIssueToolSmoke(result: IssueToolSmokeResult): string {
	const lines = [`# ${result.taskId} issue tool smoke: ${result.passed ? "PASS" : "FAIL"}`, ""];
	lines.push(`- Image: ${result.image}`);
	lines.push(`- Base commit: ${result.baseCommit}`);
	for (const check of result.checks) lines.push(`- ${check.passed ? "PASS" : "FAIL"} ${check.id}: ${check.message}`);
	return lines.join("\n");
}

export async function writeIssueManifest(manifest: IssueFrozenManifest, path: string): Promise<string> {
	const resolved = resolve(path);
	await mkdir(dirname(resolved), { recursive: true });
	await writeFile(resolved, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
	return resolved;
}

function runSummary(exitCode: number, durationMs: number, timedOut: boolean): string {
	return `exit=${exitCode}, ${(durationMs / 1_000).toFixed(2)}s${timedOut ? ", timeout" : ""}`;
}

export function renderIssueReport(results: IssueEvalResult[]): string {
	const lines = ["# Real Software Issue Agent Evaluation", ""];
	for (const result of results) {
		lines.push(
			`## ${result.taskId} / trial ${result.trialIndex} / attempt ${result.attemptIndex}: ${result.outcomeStatus}`,
		);
		lines.push("");
		lines.push(`- Run ID: ${result.runId}`);
		lines.push(`- Instance: ${result.instanceId}`);
		lines.push(`- Termination: ${result.terminationReason}`);
		lines.push(`- Provider retry eligible: ${result.providerRetryEligible}`);
		lines.push(
			`- Budget: ${result.budget.turnsUsed}/${result.budget.maxTurns} turns, wall limit ${(result.budget.maxWallTimeMs / 1_000).toFixed(0)}s`,
		);
		lines.push(`- Model: ${result.model}`);
		lines.push(`- Changed files: ${result.grade.changedFiles.map((file) => file.path).join(", ") || "none"}`);
		lines.push(
			`- Agent-added tests: ${runSummary(result.grade.agentTestsRun.exitCode, result.grade.agentTestsRun.durationMs, result.grade.agentTestsRun.timedOut)}`,
		);
		lines.push(
			`- Evaluator setup: ${runSummary(result.grade.evaluatorSetupRun.exitCode, result.grade.evaluatorSetupRun.durationMs, result.grade.evaluatorSetupRun.timedOut)}`,
		);
		lines.push(
			`- Target: ${runSummary(result.grade.targetRun.exitCode, result.grade.targetRun.durationMs, result.grade.targetRun.timedOut)}`,
		);
		lines.push(
			`- Regression: ${runSummary(result.grade.regressionRun.exitCode, result.grade.regressionRun.durationMs, result.grade.regressionRun.timedOut)}`,
		);
		for (const check of result.grade.checks) {
			lines.push(`- ${check.passed ? "PASS" : "FAIL"} ${check.id}: ${check.message}`);
		}
		if (result.runError) lines.push(`- Runtime error: ${result.runError}`);
		lines.push("");
	}
	return lines.join("\n");
}

export function renderIssuePreflight(result: IssuePreflightResult): string {
	return [
		`# ${result.taskId} issue preflight: ${result.passed ? "PASS" : "FAIL"}`,
		"",
		`- Image: ${result.image}`,
		`- Base commit: ${result.baseCommit}`,
		`- Buggy target: ${runSummary(result.buggyHiddenRun.exitCode, result.buggyHiddenRun.durationMs, result.buggyHiddenRun.timedOut)}`,
		`- Gold target: ${runSummary(result.fixedHiddenRun.exitCode, result.fixedHiddenRun.durationMs, result.fixedHiddenRun.timedOut)}`,
		`- Regression: ${runSummary(result.regressionRun.exitCode, result.regressionRun.durationMs, result.regressionRun.timedOut)}`,
	].join("\n");
}
