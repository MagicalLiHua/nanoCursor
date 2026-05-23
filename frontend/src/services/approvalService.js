export function normalizeApprovalTasks(tasks) {
  return Array.isArray(tasks)
    ? tasks.map((task) => ({
        id: task.id || task.title || String(task),
        title: task.title || task.id || String(task),
        status: task.status || "pending",
      }))
    : [];
}

export async function submitApprovalDecision(decision, context) {
  const {
    state,
    requestJson,
    render,
    handleAgentEvent,
    refreshReplayEvents,
    addTimelineEvent,
    approvalDecisionLabel,
  } = context;

  if (!decision || state.approval?.status !== "pending") return;
  const comment = document.querySelector("#approval-comment")?.value.trim() || "";
  const planId = state.approval.planId || "default-plan";
  const isToolApproval = state.approval.kind === "tool";
  const approved = decision === "approved";

  state.approval.status = "submitting";
  state.approval.decision = decision;
  state.approval.comment = comment;
  render();

  try {
    if (isToolApproval) {
      const decisionId = state.approval.decisionId;
      await requestJson(
        `/api/runs/${encodeURIComponent(state.currentThreadId)}/approvals/${encodeURIComponent(decisionId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approved, comment }),
        },
      );
      handleAgentEvent({
        type: "approval_resolved",
        title: approvalDecisionLabel(decision),
        content: comment || `工具 ${state.approval.tool || ""} ${approvalDecisionLabel(decision)}。`,
        agent: "user",
        payload: { decision_id: decisionId, decision, approved, comment },
      });
    } else {
      const event = await requestJson(`/api/runs/${encodeURIComponent(state.currentThreadId)}/approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, plan_id: planId, comment }),
      });
      handleAgentEvent(event);
    }
    await refreshReplayEvents(state.currentThreadId);
  } catch (error) {
    handleAgentEvent({
      type: "approval_resolved",
      title: approvalDecisionLabel(decision),
      content: comment || `本地已记录审批结果：${approvalDecisionLabel(decision)}。`,
      agent: "user",
      payload: { plan_id: planId, decision, comment, local_only: true },
    });
    addTimelineEvent({
      type: "error",
      title: "审批结果未能写入后端",
      content: error.message,
    });
  }
}
