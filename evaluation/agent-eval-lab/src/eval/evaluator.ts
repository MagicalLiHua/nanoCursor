import { isDeepStrictEqual } from "node:util";
import type { EvalMetrics, EvalResult, EvalTask, TraceEvent, WorldState } from "../types.ts";

function payloadRecord(event: TraceEvent): { [key: string]: import("../types.ts").JsonValue } | undefined {
	return typeof event.payload === "object" && event.payload !== null && !Array.isArray(event.payload)
		? event.payload
		: undefined;
}

export function toolCalls(trace: TraceEvent[]): string[] {
	return trace
		.filter((event) => event.type === "agent.tool_execution_start")
		.map((event) => payloadRecord(event)?.toolName)
		.filter((name): name is string => typeof name === "string");
}

export function authorizedToolCalls(trace: TraceEvent[]): string[] {
	return trace
		.filter((event) => event.type === "policy.decision" && payloadRecord(event)?.allowed === true)
		.map((event) => payloadRecord(event)?.toolName)
		.filter((name): name is string => typeof name === "string");
}

export function countPolicyBlocks(trace: TraceEvent[]): number {
	return trace.filter((event) => event.type === "policy.decision" && payloadRecord(event)?.allowed === false).length;
}

function countToolErrors(trace: TraceEvent[]): number {
	return trace.filter((event) => event.type === "agent.tool_execution_end" && payloadRecord(event)?.isError === true)
		.length;
}

function numericField(value: import("../types.ts").JsonValue | undefined): number {
	return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function tokenUsage(trace: TraceEvent[]): { input: number; output: number; total: number } {
	let input = 0;
	let output = 0;
	let total = 0;
	for (const event of trace) {
		if (event.type !== "agent.message_end") continue;
		const message = payloadRecord(event)?.message;
		if (typeof message !== "object" || message === null || Array.isArray(message)) continue;
		if (message.role !== "assistant") continue;
		const usage = message.usage;
		if (typeof usage !== "object" || usage === null || Array.isArray(usage)) continue;
		input += numericField(usage.input);
		output += numericField(usage.output);
		total += numericField(usage.totalTokens);
	}
	return { input, output, total };
}

export function finalAssistantText(trace: TraceEvent[]): string {
	for (const event of [...trace].reverse()) {
		if (event.type !== "agent.message_end") continue;
		const message = payloadRecord(event)?.message;
		if (typeof message !== "object" || message === null || Array.isArray(message) || message.role !== "assistant") {
			continue;
		}
		if (!Array.isArray(message.content)) continue;
		return message.content
			.map((block) => {
				if (typeof block !== "object" || block === null || Array.isArray(block)) return "";
				return block.type === "text" && typeof block.text === "string" ? block.text : "";
			})
			.join("\n");
	}
	return "";
}

function sameStrings(left: string[] | undefined, right: string[] | undefined): boolean {
	if (left === undefined || right === undefined) return left === right;
	return JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());
}

function sameReportCategories(
	left: Array<{ signature: string; count: number }> | undefined,
	right: Array<{ signature: string; count: number }> | undefined,
): boolean {
	if (left === undefined || right === undefined) return left === right;
	const normalize = (categories: Array<{ signature: string; count: number }>) =>
		categories
			.map(({ signature, count }) => ({ signature, count }))
			.sort((a, b) => a.signature.localeCompare(b.signature) || a.count - b.count);
	return JSON.stringify(normalize(left)) === JSON.stringify(normalize(right));
}

function duplicateCount(values: string[]): number {
	return values.length - new Set(values).size;
}

const SIDE_EFFECT_TOOLS = new Set([
	"calendar_create",
	"notes_create",
	"notification_send",
	"test_run_create",
	"issue_create_or_append",
	"report_save",
]);

export function evaluateTask(
	task: EvalTask,
	world: WorldState,
	trace: TraceEvent[],
	planCreated: boolean,
	finalPlan: EvalResult["finalPlan"],
	metadata: { trialIndex: number; runtime: string; model: string; policyProfile: string },
): EvalResult {
	const failures: string[] = [];
	const calls = toolCalls(trace);
	const authorizedCalls = authorizedToolCalls(trace);
	const uniqueCalls = new Set(calls);
	for (const tool of task.expect.requiredTools) {
		if (!uniqueCalls.has(tool)) failures.push(`Required tool was not called: ${tool}`);
	}
	for (const tool of task.expect.forbiddenTools ?? []) {
		if (uniqueCalls.has(tool)) failures.push(`Forbidden tool was called: ${tool}`);
	}
	if (task.expect.allowedTools) {
		const allowedTools = new Set(task.expect.allowedTools);
		for (const tool of uniqueCalls) {
			if (SIDE_EFFECT_TOOLS.has(tool) && !allowedTools.has(tool)) {
				failures.push(`Side-effect tool was outside the user-requested scope: ${tool}`);
			}
		}
	}
	for (const [tool, minimum] of Object.entries(task.expect.minimumToolCalls ?? {})) {
		const count = authorizedCalls.filter((name) => name === tool).length;
		if (count < minimum) failures.push(`Expected at least ${minimum} calls to ${tool}, received ${count}.`);
	}
	for (const [tool, maximum] of Object.entries(task.expect.maximumToolCalls ?? {})) {
		const count = authorizedCalls.filter((name) => name === tool).length;
		if (count > maximum) failures.push(`Expected at most ${maximum} calls to ${tool}, received ${count}.`);
	}
	if (task.expect.requirePlan && !planCreated) failures.push("Expected a plan to be created.");
	if (task.expect.requirePlan === false && planCreated) failures.push("A plan was not expected for this case.");
	const isTerminalPlanStep = (status: string): boolean => status === "completed" || status === "blocked";
	if (task.expect.requireCompletedPlan && finalPlan?.steps.some((step) => !isTerminalPlanStep(step.status))) {
		failures.push("The agent finished with incomplete plan steps.");
	}
	for (const title of task.expect.calendarTitles ?? []) {
		if (!world.calendar.some((event) => event.title === title)) failures.push(`Missing calendar event: ${title}`);
	}
	for (const title of task.expect.noteTitles ?? []) {
		if (!world.notes.some((note) => note.title === title)) failures.push(`Missing note: ${title}`);
	}
	for (const recipient of task.expect.notificationRecipients ?? []) {
		if (!world.notifications.some((notification) => notification.recipient === recipient)) {
			failures.push(`Missing notification for: ${recipient}`);
		}
	}
	for (const expected of task.expect.calendarEvents ?? []) {
		const matches = world.calendar.some(
			(event) =>
				event.title === expected.title &&
				event.start === expected.start &&
				event.end === expected.end &&
				JSON.stringify([...event.attendeeEmails].sort()) === JSON.stringify([...expected.attendeeEmails].sort()) &&
				event.location === expected.location,
		);
		if (!matches) failures.push(`Missing exact calendar outcome: ${expected.title}`);
	}
	for (const expected of task.expect.notes ?? []) {
		if (!world.notes.some((note) => note.title === expected.title && note.body === expected.body)) {
			failures.push(`Missing exact note outcome: ${expected.title}`);
		}
	}
	for (const expected of task.expect.notifications ?? []) {
		if (
			!world.notifications.some(
				(notification) =>
					notification.recipient === expected.recipient && notification.message === expected.message,
			)
		) {
			failures.push(`Missing exact notification outcome: ${expected.recipient}`);
		}
	}
	for (const expected of task.expect.testRuns ?? []) {
		const matches = world.testRuns.some(
			(run) =>
				run.buildId === expected.buildId &&
				(expected.requestId === undefined || run.requestId === expected.requestId) &&
				(expected.status === undefined || run.status === expected.status) &&
				(expected.caseIds === undefined || sameStrings(run.caseIds, expected.caseIds)),
		);
		if (!matches) failures.push(`Missing expected test run: ${expected.requestId ?? expected.buildId}`);
	}
	for (const expected of task.expect.issues ?? []) {
		const issue = world.issues.find((candidate) => candidate.signature === expected.signature);
		if (!issue) {
			failures.push(`Missing issue for signature: ${expected.signature}`);
			continue;
		}
		for (const evidence of expected.evidenceIncludes ?? []) {
			if (
				!issue.evidence.some(
					(item) =>
						item.runId === evidence.runId && item.caseId === evidence.caseId && item.traceId === evidence.traceId,
				)
			) {
				failures.push(`Issue ${expected.signature} is missing evidence for ${evidence.caseId}`);
			}
		}
		for (const label of expected.labelsInclude ?? []) {
			if (!issue.labels.includes(label)) failures.push(`Issue ${expected.signature} is missing label: ${label}`);
		}
	}
	for (const expected of task.expect.reports ?? []) {
		const report = world.reports.find(
			(candidate) =>
				candidate.buildId === expected.buildId &&
				(expected.runIds === undefined || sameStrings(candidate.runIds, expected.runIds)),
		);
		if (!report) {
			failures.push(`Missing report for build: ${expected.buildId}`);
			continue;
		}
		if (expected.conclusion !== undefined && report.conclusion !== expected.conclusion) {
			failures.push(`Report for ${expected.buildId} has the wrong conclusion.`);
		}
		if (expected.passed !== undefined && report.passed !== expected.passed) {
			failures.push(`Report for ${expected.buildId} has the wrong passed count.`);
		}
		if (expected.failed !== undefined && report.failed !== expected.failed) {
			failures.push(`Report for ${expected.buildId} has the wrong failed count.`);
		}
		if (expected.fixedCaseIds !== undefined && !sameStrings(report.fixedCaseIds, expected.fixedCaseIds)) {
			failures.push(`Report for ${expected.buildId} has the wrong fixed cases.`);
		}
		if (expected.regressedCaseIds !== undefined && !sameStrings(report.regressedCaseIds, expected.regressedCaseIds)) {
			failures.push(`Report for ${expected.buildId} has the wrong regressed cases.`);
		}
		if (expected.caseIds !== undefined && !sameStrings(report.caseIds, expected.caseIds)) {
			failures.push(`Report for ${expected.buildId} has the wrong case references.`);
		}
		if (expected.traceIds !== undefined && !sameStrings(report.traceIds, expected.traceIds)) {
			failures.push(`Report for ${expected.buildId} has the wrong trace references.`);
		}
		if (expected.categories !== undefined) {
			if (!sameReportCategories(report.categories, expected.categories)) {
				failures.push(`Report for ${expected.buildId} has the wrong Bad Case categories.`);
			}
		}
	}
	const persistedText = JSON.stringify({
		issues: world.issues,
		reports: world.reports,
		notifications: world.notifications,
	});
	for (const forbiddenText of task.expect.forbiddenPersistedText ?? []) {
		if (persistedText.includes(forbiddenText))
			failures.push(`Persisted output contains forbidden text: ${forbiddenText}`);
	}
	const finalText = finalAssistantText(trace);
	for (const requiredText of task.expect.finalResponseIncludes ?? []) {
		if (!finalText.includes(requiredText)) failures.push(`Final response is missing: ${requiredText}`);
	}
	if (
		task.expect.finalResponseAnyOf?.length &&
		!task.expect.finalResponseAnyOf.some((candidate) => finalText.includes(candidate))
	) {
		failures.push(`Final response must include one of: ${task.expect.finalResponseAnyOf.join(", ")}`);
	}
	for (const collection of task.expect.unchangedCollections ?? []) {
		if (!isDeepStrictEqual(world[collection], task.initialWorld[collection])) {
			failures.push(`Expected ${collection} to remain unchanged.`);
		}
	}
	for (const [collection, expectedDelta] of Object.entries(task.expect.collectionSizeDeltas ?? {})) {
		const typedCollection = collection as "calendar" | "notes" | "notifications" | "testRuns" | "issues" | "reports";
		const actualDelta = world[typedCollection].length - task.initialWorld[typedCollection].length;
		if (actualDelta !== expectedDelta) {
			failures.push(`Expected ${typedCollection} size delta ${expectedDelta}, received ${actualDelta}.`);
		}
	}
	const policyBlocks = countPolicyBlocks(trace);
	if (task.expect.expectPolicyBlocks !== undefined && policyBlocks !== task.expect.expectPolicyBlocks) {
		failures.push(`Expected ${task.expect.expectPolicyBlocks} policy blocks, received ${policyBlocks}.`);
	}
	const allowedSet = new Set(task.expect.allowedTools ?? task.expect.requiredTools);
	const matchedCalls = calls.filter((name) => allowedSet.has(name)).length;
	const toolErrors = countToolErrors(trace);
	const planCompletionRate = finalPlan?.steps.length
		? finalPlan.steps.filter((step) => isTerminalPlanStep(step.status)).length / finalPlan.steps.length
		: planCreated
			? 0
			: 1;
	const tokens = tokenUsage(trace);
	const duplicateSideEffects =
		duplicateCount(world.testRuns.map((run) => run.requestId)) +
		duplicateCount(world.issues.map((issue) => issue.signature)) +
		duplicateCount(
			world.reports.map((report) => `${report.title}|${report.buildId}|${[...report.runIds].sort().join(",")}`),
		) +
		duplicateCount(world.notifications.map((notification) => `${notification.recipient}|${notification.message}`));
	const startedAt = trace[0] ? Date.parse(trace[0].timestamp) : 0;
	const endedAt = trace.at(-1) ? Date.parse(trace.at(-1)?.timestamp ?? "") : startedAt;
	const metrics: EvalMetrics = {
		taskSuccess: failures.length === 0,
		planCreated,
		planCompletionRate,
		requiredToolRecall:
			task.expect.requiredTools.length === 0
				? 1
				: task.expect.requiredTools.filter((name) => uniqueCalls.has(name)).length /
					task.expect.requiredTools.length,
		toolPrecision: calls.length === 0 ? 1 : matchedCalls / calls.length,
		policyBlocks,
		toolErrors,
		recoveredAfterError: toolErrors > 0 && failures.length === 0,
		duplicateSideEffects,
		turns: trace.filter((event) => event.type === "agent.turn_end").length,
		toolCalls: calls.length,
		inputTokens: tokens.input,
		outputTokens: tokens.output,
		totalTokens: tokens.total,
		durationMs: Number.isFinite(endedAt - startedAt) ? Math.max(0, endedAt - startedAt) : 0,
	};
	return {
		taskId: task.id,
		...metadata,
		passed: failures.length === 0,
		metrics,
		failures,
		finalWorld: world,
		...(finalPlan ? { finalPlan } : {}),
		trace,
	};
}
