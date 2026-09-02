import type {
	FaultRule,
	IssueEvidence,
	ReportCategory,
	ReportConclusion,
	TestReport,
	TestRun,
	ToolInvocation,
	WorldState,
} from "../types.ts";

export type FaultEventSink = (invocation: ToolInvocation, fault: FaultRule) => void;

export class WorldSandbox {
	private readonly state: WorldState;
	private readonly faults: FaultRule[];
	private readonly callCounts = new Map<string, number>();
	private readonly timezoneOffset: string;
	private readonly onFault?: FaultEventSink;
	private readonly postCommitFaults = new Map<string, FaultRule>();

	constructor(initialState: WorldState, faults: FaultRule[] = [], onFault?: FaultEventSink) {
		this.state = structuredClone(initialState);
		this.faults = structuredClone(faults);
		this.timezoneOffset = initialState.now.match(/([+-]\d{2}:\d{2})$/)?.[1] ?? "Z";
		this.onFault = onFault;
	}

	getState(): WorldState {
		return structuredClone(this.state);
	}

	nextInvocation(toolCallId: string, toolName: string, args: ToolInvocation["args"]): ToolInvocation {
		const callIndex = (this.callCounts.get(toolName) ?? 0) + 1;
		this.callCounts.set(toolName, callIndex);
		return { toolCallId, toolName, args, callIndex };
	}

	applyFault(invocation: ToolInvocation): FaultRule | undefined {
		const fault = this.faults.find(
			(candidate) => candidate.toolName === invocation.toolName && candidate.onCall === invocation.callIndex,
		);
		if (!fault) return undefined;
		this.onFault?.(structuredClone(invocation), structuredClone(fault));
		const fallback: Record<FaultRule["mode"], string> = {
			error: "Injected tool failure.",
			timeout: "Injected timeout.",
			timeout_after_commit: "Injected timeout after commit.",
			empty_result: "Injected empty result.",
			malformed_result: "Injected malformed result.",
			permission_denied: "Injected permission denial.",
			rate_limited: "Injected rate limit.",
		};
		if (fault.mode === "timeout_after_commit") {
			this.postCommitFaults.set(invocation.toolCallId, structuredClone(fault));
			return undefined;
		}
		if (fault.mode === "empty_result" || fault.mode === "malformed_result") return structuredClone(fault);
		throw new Error(fault.message?.trim() || fallback[fault.mode]);
	}

	throwPostCommitFault(toolCallId: string): void {
		const fault = this.postCommitFaults.get(toolCallId);
		if (!fault) return;
		this.postCommitFaults.delete(toolCallId);
		throw new Error(fault.message?.trim() || "Injected timeout after commit.");
	}

	findContacts(query: string) {
		const normalized = query.trim().toLowerCase();
		return this.state.contacts.filter((contact) =>
			[contact.name, contact.email, ...contact.tags].some((value) => value.toLowerCase().includes(normalized)),
		);
	}

	hasContactEmail(email: string): boolean {
		return this.state.contacts.some((contact) => contact.email.toLowerCase() === email.trim().toLowerCase());
	}

	listCalendar(start: string, end: string) {
		const normalizedStart = this.normalizeDateTime(start);
		const normalizedEnd = this.normalizeDateTime(end);
		return this.state.calendar.filter((event) => event.end > normalizedStart && event.start < normalizedEnd);
	}

	getWeather(location: string, date: string) {
		return this.state.weather.find(
			(forecast) => forecast.location.toLowerCase() === location.trim().toLowerCase() && forecast.date === date,
		);
	}

	getRequirement(id: string) {
		return structuredClone(this.state.requirements.find((requirement) => requirement.id === id));
	}

	getTestRun(input: { runId?: string; requestId?: string }) {
		if (!input.runId && !input.requestId) throw new Error("Provide run_id or request_id.");
		return structuredClone(
			this.state.testRuns.find(
				(run) =>
					(input.runId ? run.id === input.runId : true) &&
					(input.requestId ? run.requestId === input.requestId : true),
			),
		);
	}

	createTestRun(buildId: string, caseIds: string[], requestId: string): TestRun {
		const existing = this.state.testRuns.find((run) => run.requestId === requestId);
		if (existing) return structuredClone(existing);
		const run: TestRun = {
			id: `run-${this.state.testRuns.length + 1}`,
			buildId: buildId.trim(),
			status: "QUEUED",
			requestId: requestId.trim(),
			caseIds: [...caseIds],
			results: [],
			createdAt: this.state.now,
		};
		this.state.testRuns.push(run);
		return structuredClone(run);
	}

	getExecutionTrace(id: string) {
		return structuredClone(this.state.executionTraces.find((trace) => trace.id === id));
	}

	searchIssues(signature: string) {
		return structuredClone(this.state.issues.filter((issue) => issue.signature === signature));
	}

	createOrAppendIssue(input: { signature: string; title: string; evidence: IssueEvidence[]; labels: string[] }) {
		const existing = this.state.issues.find(
			(issue) => issue.signature === input.signature && issue.status === "OPEN",
		);
		if (existing) {
			for (const evidence of input.evidence) {
				if (
					!existing.evidence.some(
						(item) =>
							item.runId === evidence.runId &&
							item.caseId === evidence.caseId &&
							item.traceId === evidence.traceId,
					)
				) {
					existing.evidence.push(structuredClone(evidence));
				}
			}
			existing.labels = [...new Set([...existing.labels, ...input.labels])];
			return { action: "appended" as const, issue: structuredClone(existing) };
		}
		const issue = {
			id: `issue-${this.state.issues.length + 1}`,
			signature: input.signature,
			title: input.title.trim(),
			status: "OPEN" as const,
			evidence: structuredClone(input.evidence),
			labels: [...new Set(input.labels)],
		};
		this.state.issues.push(issue);
		return { action: "created" as const, issue: structuredClone(issue) };
	}

	saveReport(input: {
		title: string;
		buildId: string;
		runIds: string[];
		conclusion: ReportConclusion;
		summary: string;
		passed?: number;
		failed?: number;
		fixedCaseIds?: string[];
		regressedCaseIds?: string[];
		categories?: ReportCategory[];
		caseIds?: string[];
		traceIds?: string[];
	}) {
		const report: TestReport = {
			id: `report-${this.state.reports.length + 1}`,
			title: input.title.trim(),
			buildId: input.buildId.trim(),
			runIds: [...input.runIds],
			conclusion: input.conclusion,
			summary: input.summary.trim(),
			...(input.passed !== undefined ? { passed: input.passed } : {}),
			...(input.failed !== undefined ? { failed: input.failed } : {}),
			...(input.fixedCaseIds ? { fixedCaseIds: [...input.fixedCaseIds] } : {}),
			...(input.regressedCaseIds ? { regressedCaseIds: [...input.regressedCaseIds] } : {}),
			...(input.categories ? { categories: structuredClone(input.categories) } : {}),
			...(input.caseIds ? { caseIds: [...input.caseIds] } : {}),
			...(input.traceIds ? { traceIds: [...input.traceIds] } : {}),
			createdAt: this.state.now,
		};
		this.state.reports.push(report);
		return structuredClone(report);
	}

	createCalendarEvent(input: Omit<WorldState["calendar"][number], "id">) {
		const start = this.normalizeDateTime(input.start);
		const end = this.normalizeDateTime(input.end);
		if (start >= end) throw new Error("Calendar event end must be after start.");
		const event = { id: `event-${this.state.calendar.length + 1}`, ...structuredClone(input), start, end };
		this.state.calendar.push(event);
		return structuredClone(event);
	}

	createNote(title: string, body: string) {
		const note = {
			id: `note-${this.state.notes.length + 1}`,
			title: title.trim(),
			body: body.trim(),
			createdAt: this.state.now,
		};
		this.state.notes.push(note);
		return structuredClone(note);
	}

	sendNotification(recipient: string, message: string) {
		const notification = {
			id: `notification-${this.state.notifications.length + 1}`,
			recipient: recipient.trim(),
			message: message.trim(),
			createdAt: this.state.now,
		};
		this.state.notifications.push(notification);
		return structuredClone(notification);
	}

	private normalizeDateTime(value: string): string {
		const trimmed = value.trim();
		const withSeconds = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(trimmed) ? `${trimmed}:00` : trimmed;
		return /(Z|[+-]\d{2}:\d{2})$/.test(withSeconds) ? withSeconds : `${withSeconds}${this.timezoneOffset}`;
	}
}
