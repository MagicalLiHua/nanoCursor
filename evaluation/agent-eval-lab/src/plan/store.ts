import type { PlanSnapshot, PlanStep, PlanStepStatus } from "../types.ts";

export class PlanStore {
	private snapshot?: PlanSnapshot;
	private readonly now: () => Date;

	constructor(now: () => Date = () => new Date()) {
		this.now = now;
	}

	create(objective: string, titles: string[]): PlanSnapshot {
		const normalizedObjective = objective.trim();
		const normalizedTitles = titles.map((title) => title.trim()).filter((title) => title.length > 0);
		if (!normalizedObjective) throw new Error("Plan objective must not be empty.");
		if (normalizedTitles.length === 0) throw new Error("Plan must contain at least one step.");
		this.snapshot = {
			objective: normalizedObjective,
			revision: 1,
			steps: normalizedTitles.map((title, index) => ({
				id: `step-${index + 1}`,
				title,
				status: index === 0 ? "in_progress" : "pending",
			})),
			updatedAt: this.now().toISOString(),
		};
		return this.getRequired();
	}

	update(stepId: string, status: PlanStepStatus, note?: string): PlanSnapshot {
		const current = this.getRequired();
		const index = current.steps.findIndex((step) => step.id === stepId);
		if (index < 0) throw new Error(`Unknown plan step: ${stepId}`);
		const steps = current.steps.map((step, stepIndex): PlanStep => {
			if (stepIndex !== index) return { ...step };
			return { ...step, status, ...(note?.trim() ? { note: note.trim() } : {}) };
		});
		this.snapshot = {
			...current,
			revision: current.revision + 1,
			steps,
			updatedAt: this.now().toISOString(),
		};
		return this.getRequired();
	}

	get(): PlanSnapshot | undefined {
		return this.snapshot ? structuredClone(this.snapshot) : undefined;
	}

	hasActiveStep(): boolean {
		return this.snapshot?.steps.some((step) => step.status === "in_progress") ?? false;
	}

	private getRequired(): PlanSnapshot {
		if (!this.snapshot) throw new Error("Create a plan before updating it.");
		return structuredClone(this.snapshot);
	}
}
