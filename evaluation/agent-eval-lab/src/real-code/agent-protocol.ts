import type { RealCodeRunOptions, RealCodeTask, RealCodeTerminationReason } from "./types.ts";

export function realCodeRunLimits(task: RealCodeTask, options: RealCodeRunOptions = {}) {
	return {
		maxTurns: options.maxTurns ?? (task.mode === "agent-task" ? 64 : 24),
		...(options.maxWallTimeMs !== undefined || task.mode === "agent-task"
			? { maxWallTimeMs: options.maxWallTimeMs ?? 20 * 60_000 }
			: {}),
	};
}

export function classifyTermination(input: {
	wallTimeLimitReached: boolean;
	runError?: string;
	turnLimitReached: boolean;
	lastAssistantStopReason?: string;
}): RealCodeTerminationReason {
	if (input.wallTimeLimitReached) return "wall-time-limit";
	if (input.runError || input.lastAssistantStopReason === "error" || input.lastAssistantStopReason === "aborted") {
		return "runtime-error";
	}
	if (input.turnLimitReached) return "turn-limit";
	if (input.lastAssistantStopReason === "length") return "output-limit";
	return "completed";
}
