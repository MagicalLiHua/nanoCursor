import React from "react";
import { statusLabel } from "../../core/format.js";
import { ClipboardList } from "lucide-react";

function normalizeTaskTitle(title = "") {
  return String(title).replace(/\s+/g, "").replace(/[：:]/g, ":").trim();
}

function isDuplicateGeneratedStageTask(task, allTasks = []) {
  if (task.source === "execution_plan") return false;
  const title = normalizeTaskTitle(task.title);
  const match = title.match(/^阶段\d+\s*[:：]\s*(.+)$/);
  if (!match) return false;
  const duplicateStageTitle = match[1];
  return allTasks.some(
    (c) => c.source === "execution_plan" && normalizeTaskTitle(c.title) === normalizeTaskTitle(duplicateStageTitle),
  );
}

function isVisibleTask(task, allTasks = []) {
  if (!String(task?.title || "").trim() && !String(task?.description || "").trim()) return false;
  return !isDuplicateGeneratedStageTask(task, allTasks);
}

function TaskCard({ task, inferTaskCapabilities, capabilityDisplayName }) {
  const capabilities = task.capabilities || inferTaskCapabilities?.(task) || [];
  const evidence = Array.isArray(task.toolEvidence) ? task.toolEvidence : [];
  return (
    <article className={`task-card ${task.status || "idle"}`}>
      <div className="task-top">
        <div className="task-title">{task.title}</div>
        <span className={`badge ${task.status}`}>{statusLabel(task.status)}</span>
      </div>
      <p className="task-desc">{task.description}</p>
      <div className="task-meta-row">
        <span className="panel-subtitle">负责人：{task.owner}</span>
        {task.failure && <span className="task-failure">失败原因：{task.failure}</span>}
        {capabilities.length > 0 && (
          <div className="task-capabilities">
            {capabilities.slice(0, 4).map((id, i) => (
              <span key={i}>{capabilityDisplayName?.(id) || id}</span>
            ))}
          </div>
        )}
        {evidence.length > 0 && (
          <div className="task-evidence">
            {evidence.slice(-4).map((item, i) => (
              <span key={i} title={item.capabilityId || item.capability_id || ""}>
                {item.tool || "tool"}
              </span>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

export default function Tasks({ state, inferTaskCapabilities, capabilityDisplayName, onToggleCompleted }) {
  const visibleTasks = state.tasks.filter((t) => isVisibleTask(t, state.tasks));
  const allCompleted = visibleTasks.length > 0 && visibleTasks.every((t) => ["completed", "skipped"].includes(t.status));
  const archiveCompleted = state.status === "completed" && allCompleted && !state.showCompletedTasks;

  if (archiveCompleted) {
    return (
      <div className="task-list">
        <section className="task-archive-summary">
          <div>
            <strong>{visibleTasks.length}</strong>
            <span>任务已完成并归档</span>
          </div>
          <button className="button secondary compact-button" onClick={onToggleCompleted} type="button">查看任务</button>
        </section>
      </div>
    );
  }

  const isRunning = ["running", "waiting_approval", "cancelling"].includes(state.status);

  return (
    <div className="task-list">
      {state.status === "completed" && allCompleted && (
        <section className="task-archive-summary open">
          <div>
            <strong>{visibleTasks.length}</strong>
            <span>已展开完成任务</span>
          </div>
          <button className="button secondary compact-button" onClick={onToggleCompleted} type="button">收起任务</button>
        </section>
      )}
      {visibleTasks.length ? (
        visibleTasks.map((task, i) => (
          <TaskCard key={task.id || i} task={task} inferTaskCapabilities={inferTaskCapabilities} capabilityDisplayName={capabilityDisplayName} />
        ))
      ) : (
        <div className="empty-card">
          <div className="empty-card-icon"><ClipboardList size={28} /></div>
          <strong>{isRunning ? "任务生成中" : "暂无任务"}</strong>
          <p>
            {isRunning
              ? "Lead 会拆解需求、创建任务并分配给合适的 Agent。任务会在这里实时更新。"
              : "发送一个需求后，Lead 会自动拆解并生成任务清单，分配给 Planner、Coder、Tester 等 Agent。"}
          </p>
        </div>
      )}
    </div>
  );
}
