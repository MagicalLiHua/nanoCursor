export function createRunStateController({
  state,
  render,
  addTimelineEvent,
  fetchJson,
  loadWorkspaceDataSnapshot,
  fileType,
  mapBackendTasks,
  mapBackendTeam,
  normalizeTask,
  tasksFromExecutionPlan,
}) {
  function syncTasksFromExecutionPlan(executionPlan) {
    tasksFromExecutionPlan(executionPlan).forEach((task) => upsertTask(task));
  }

  function taskForStageId(stageId) {
    if (!stageId) return null;
    return state.tasks.find((task) => task.id === stageId || String(task.id || "").endsWith(`-${stageId}`));
  }

  function upsertTask(task) {
    if (!task?.id) return;
    const existing = state.tasks.find((item) => item.id === task.id);
    const normalized = normalizeTask(task);
    if (!normalized) return;

    if (existing) {
      if (
        ["completed", "failed", "cancelled", "skipped"].includes(existing.status) &&
        ["pending", "in_progress", "running"].includes(normalized.status)
      ) {
        normalized.status = existing.status;
      }
      Object.assign(existing, normalized);
    } else {
      state.tasks.push(normalized);
    }
    state.metrics.tasks = state.tasks.length;
  }

  function patchTask(taskId, patch) {
    if (!taskId) return;
    const task = state.tasks.find((item) => item.id === taskId);
    if (task) {
      const normalized = normalizeTask({ ...task, ...patch, id: taskId });
      if (normalized) Object.assign(task, normalized);
      return;
    }
    const normalized = normalizeTask({ title: patch.title || taskId, ...patch, id: taskId });
    if (!normalized) return;
    state.tasks.push(normalized);
    state.metrics.tasks = state.tasks.length;
  }

  function settleTasksForRunStatus(status) {
    if (status !== "completed") return;
    state.tasks.forEach((task) => {
      if (["pending", "in_progress", "running"].includes(task.status)) {
        task.status = "completed";
      }
    });
  }

  function patchStageTask(stageUpdate = {}) {
    const stageId = stageUpdate.stage_id || stageUpdate.stageId || "";
    if (!stageId) return;
    const existing = taskForStageId(stageId);
    const taskId = existing?.id || `stage-${String(state.tasks.length + 1).padStart(2, "0")}-${stageId}`;
    patchTask(taskId, {
      title: stageUpdate.title || existing?.title || stageId,
      description: stageUpdate.description || existing?.description || "",
      status: stageUpdate.status || existing?.status || "pending",
      owner: stageUpdate.owner || existing?.owner || "Agent",
      failure: stageUpdate.failure || existing?.failure || "",
    });
  }

  function attachToolEvidenceToTask(stageId, evidence) {
    const task = taskForStageId(stageId);
    if (!task) return;
    const toolEvidence = Array.isArray(task.toolEvidence) ? task.toolEvidence : [];
    task.toolEvidence = [...toolEvidence, evidence].slice(-12);
  }

  function upsertFile(file) {
    const path = typeof file === "string" ? file : file?.path;
    if (!path) return;
    state.files = state.files.map((item) => ({ ...item, active: false }));
    const existing = state.files.find((item) => item.path === path);
    if (existing) {
      existing.active = true;
      existing.type = fileType(path);
    } else {
      state.files.unshift({
        path,
        type: fileType(path),
        active: true,
      });
    }
    state.metrics.files = state.files.length;
  }

  async function refreshWorkspaceData({ allowEmpty = false, announce = false, includeRunState = true } = {}) {
    const hasFocusedRun = Boolean(state.currentThreadId && state.currentThreadId !== "pending");
    const shouldUpdateRunState = includeRunState && !hasFocusedRun;
    const { filesResult, tasksResult, teamResult } = await loadWorkspaceDataSnapshot({
      fetchJson,
      includeRunState: shouldUpdateRunState,
    });

    if (filesResult.status === "fulfilled") {
      const files = filesResult.value.files || [];
      if (files.length || allowEmpty) {
        state.files = files.slice(0, 80).map((file, index) => ({
          path: file.path,
          type: fileType(file.path, file.is_dir),
          active: index === 0,
        }));
        state.metrics.files = files.filter((file) => !file.is_dir).length;
      }
    }

    if (shouldUpdateRunState && tasksResult.status === "fulfilled") {
      const tasks = tasksResult.value.tasks || [];
      if (tasks.length || allowEmpty) {
        state.tasks = mapBackendTasks(tasks);
        state.metrics.tasks = tasks.length;
      }
    }

    if (shouldUpdateRunState && teamResult.status === "fulfilled") {
      const members = teamResult.value.members || [];
      if (members.length || allowEmpty) {
        state.team = mapBackendTeam(members);
      }
    }

    if (announce) {
      addTimelineEvent({
        type: "metrics_updated",
        title: "工作区数据已同步",
        content: "文件、任务和团队状态已从后端刷新。",
      });
    } else {
      render();
    }
  }

  return {
    attachToolEvidenceToTask,
    patchStageTask,
    patchTask,
    refreshWorkspaceData,
    settleTasksForRunStatus,
    syncTasksFromExecutionPlan,
    taskForStageId,
    upsertFile,
    upsertTask,
  };
}
