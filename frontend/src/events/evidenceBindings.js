export function bindEvidenceEvents(context) {
  const {
    state,
    render,
    withBusyAction,
    startReplay,
    pauseReplay,
    resetReplayToStart,
    setReplaySpeed,
    submitApprovalDecision,
    addTimelineEvent,
    buildReportText,
    showToast,
    requestJson,
    loadRecoveryCenter,
    runBenchmark,
  } = context;

  document.querySelectorAll("[data-action='select-diff-file']").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedDiffFile = button.dataset.path;
      render();
    });
  });

  document.querySelector("[data-action='replay-play']")?.addEventListener("click", () => {
    startReplay();
  });

  document.querySelector("[data-action='replay-pause']")?.addEventListener("click", () => {
    pauseReplay();
  });

  document.querySelector("[data-action='replay-reset']")?.addEventListener("click", () => {
    resetReplayToStart();
  });

  document.querySelector("[data-action='replay-speed']")?.addEventListener("change", (event) => {
    setReplaySpeed(Number(event.target.value) || 1);
  });

  document.querySelectorAll("[data-action='approval-decision']").forEach((button) => {
    button.addEventListener("click", async () => {
      await submitApprovalDecision(button.dataset.decision);
    });
  });

  document.querySelector("[data-action='copy-report']")?.addEventListener("click", async () => {
    await withBusyAction("copy-report", async () => {
      const reportText = buildReportText();
      await navigator.clipboard?.writeText(reportText);
      addTimelineEvent({
        type: "report_ready",
        title: "报告已复制",
        content: "交付报告已写入剪贴板。",
      });
      showToast("success", "报告已复制");
    });
  });

  document.querySelectorAll("[data-action='execute-recovery']").forEach((button) => {
    button.addEventListener("click", async () => {
      const actionId = button.dataset.actionId;
      const target = button.dataset.target || "";
      const targetPath = button.dataset.targetPath || "";
      const needsConfirm = actionId === "restore-backup" || actionId === "create-remediation-run";
      const confirmed = needsConfirm ? confirm(`确认执行 "${actionId}" 动作？`) : true;
      if (!confirmed) return;
      try {
        const threadId = state.currentThreadId || "";
        const result = await requestJson(
          `/api/runs/${encodeURIComponent(threadId)}/recovery/actions/${encodeURIComponent(actionId)}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action_id: actionId, target, target_path: targetPath, confirmed: true }),
          }
        );
        addTimelineEvent({
          type: "recovery_action_completed",
          title: `恢复动作: ${actionId}`,
          content: result.message || result.status,
          payload: { result },
        });
        loadRecoveryCenter();
      } catch (error) {
        addTimelineEvent({ type: "error", title: "恢复动作执行失败", content: error.message });
      }
      render();
    });
  });

  document.querySelector("[data-action='create-remediation']")?.addEventListener("click", async () => {
    const instruction = document.querySelector("#remediation-instruction")?.value?.trim() || "";
    const threadId = state.currentThreadId || "";
    if (!threadId) return;
    if (!confirm("确认基于当前失败的 run 创建补救 run？")) return;
    try {
      const result = await requestJson(`/api/runs/${encodeURIComponent(threadId)}/remediation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction, failure_id: "" }),
      });
      addTimelineEvent({
        type: "remediation_created",
        title: "补救 Run 已创建",
        content: `新 run: ${result.thread_id}`,
        payload: { result },
      });
      const remediationInput = document.querySelector("#remediation-instruction");
      if (remediationInput) remediationInput.value = "";
    } catch (error) {
      addTimelineEvent({ type: "error", title: "创建补救 run 失败", content: error.message });
    }
    render();
  });

  document.querySelectorAll("[data-action='run-benchmark']").forEach((button) => {
    button.addEventListener("click", async () => {
      await runBenchmark(button.dataset.benchmarkId);
    });
  });
}
