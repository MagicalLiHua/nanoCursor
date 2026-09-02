import type { EvalTask } from "../types.ts";

export const SIDE_EFFECT_TOOL_NAMES = [
	"calendar_create",
	"notes_create",
	"notification_send",
	"test_run_create",
	"issue_create_or_append",
	"report_save",
] as const;

export function authorizedWriteTools(task: EvalTask): string[] {
	if (task.authorization) return [...task.authorization.allowedWriteTools];
	const legacyAllowedTools = new Set(task.expect.allowedTools ?? task.expect.requiredTools);
	return SIDE_EFFECT_TOOL_NAMES.filter((toolName) => legacyAllowedTools.has(toolName));
}
