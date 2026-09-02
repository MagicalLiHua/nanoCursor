import type { EvalTask } from "../types.ts";
import { getCollaborationHeldoutTasks as getCollaborationHeldoutV1Tasks } from "./collaboration-heldout-v1.ts";

function taskById(tasks: EvalTask[], taskId: string): EvalTask {
	const task = tasks.find((candidate) => candidate.id === taskId);
	if (!task) throw new Error(`Missing heldout task: ${taskId}`);
	return task;
}

export function getCollaborationHeldoutTasks(): EvalTask[] {
	const tasks = getCollaborationHeldoutV1Tasks();

	const retryBeforeCommit = taskById(tasks, "heldout-08");
	retryBeforeCommit.prompt =
		"为 build-41 创建 case-retry-a、case-retry-b、case-retry-c 的测试运行；将 request_id 固定为 heldout-before-commit。首次超时可使用完全相同的 request_id 重试一次。";

	for (const taskId of ["heldout-09", "heldout-12"]) {
		const task = taskById(tasks, taskId);
		task.requiresModelReview = true;
		delete task.expect.notifications;
		task.expect.notificationRecipients = ["qa-lead@example.com"];
	}

	return tasks;
}
