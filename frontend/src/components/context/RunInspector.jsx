import React from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Clock3,
  GitBranch,
  GitCompare,
  HardDrive,
  Loader2,
  Plug,
  Search,
  Settings2,
  ShieldCheck,
  Terminal,
  Wrench,
} from "lucide-react";
import { shortPath, statusLabel } from "../../core/format.js";

function normalizeTaskTitle(title = "") {
  return String(title).replace(/\s+/g, "").replace(/[：:]/g, ":").trim();
}

function isDuplicateGeneratedStageTask(task, allTasks = []) {
  if (task.source === "execution_plan") return false;
  const title = normalizeTaskTitle(task.title);
  const match = title.match(/^阶段\d+\s*[:：]\s*(.+)$/);
  if (!match) return false;
  return allTasks.some(
    (candidate) =>
      candidate.source === "execution_plan" && normalizeTaskTitle(candidate.title) === normalizeTaskTitle(match[1]),
  );
}

function isVisibleTask(task, allTasks = []) {
  if (!String(task?.title || "").trim() && !String(task?.description || "").trim()) return false;
  return !isDuplicateGeneratedStageTask(task, allTasks);
}

function statusScore(status = "") {
  if (["completed", "skipped", "passed"].includes(status)) return 4;
  if (["running", "in_progress", "waiting_approval"].includes(status)) return 3;
  if (["failed", "blocked", "error"].includes(status)) return 2;
  if (["pending", "ready", "planned"].includes(status)) return 1;
  return 0;
}

function uniqueVisibleTasks(tasks = []) {
  const byKey = new Map();
  for (const task of tasks.filter((item) => isVisibleTask(item, tasks))) {
    const key = `${normalizeTaskTitle(task.title)}::${String(task.owner || task.agent_role || "").toLowerCase()}`;
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, task);
      continue;
    }
    const existingIsPlan = existing.source === "execution_plan";
    const taskIsPlan = task.source === "execution_plan";
    if (
      existingIsPlan && !taskIsPlan
      || statusScore(task.status) > statusScore(existing.status)
      || String(task.id || "").length > String(existing.id || "").length && statusScore(task.status) === statusScore(existing.status)
    ) {
      byKey.set(key, task);
    }
  }
  return Array.from(byKey.values());
}

function sessionSnapshot(state) {
  const snapshot = state.runSnapshot;
  const snapshotThreadId = snapshot?.run?.thread_id;
  const currentThreadId = state.currentThreadId;
  if (!snapshot || !snapshotThreadId || !currentThreadId || currentThreadId === "pending") return null;
  return snapshotThreadId === currentThreadId ? snapshot : null;
}

function hasConcreteRunContext(state) {
  const threadId = String(state.currentThreadId || "");
  if (sessionSnapshot(state)) return true;
  if (!threadId || threadId === "pending" || threadId.startsWith("draft-") || threadId.startsWith("conv-")) {
    return false;
  }
  return ["running", "waiting_approval", "cancelling", "completed", "failed", "cancelled"].includes(state.status);
}

function taskPriority(task) {
  const status = task?.status || "pending";
  if (["running", "in_progress", "waiting_approval"].includes(status)) return 0;
  if (["failed", "blocked", "error"].includes(status)) return 1;
  if (["pending", "ready", "planned"].includes(status)) return 2;
  if (["completed", "skipped"].includes(status)) return 3;
  return 4;
}

function TaskIcon({ status }) {
  if (["running", "in_progress", "waiting_approval"].includes(status)) {
    return <Loader2 className="codex-spin" size={16} />;
  }
  if (["completed", "skipped"].includes(status)) return <CheckCircle2 size={16} />;
  if (["failed", "blocked", "error"].includes(status)) return <AlertTriangle size={16} />;
  return <Circle size={16} />;
}

function Section({ title, action, children }) {
  return (
    <section className="codex-inspector-section">
      <div className="codex-section-head">
        <h4>{title}</h4>
        {action}
      </div>
      {children}
    </section>
  );
}

function InfoRow({ icon: Icon, label, value, muted = false, action }) {
  return (
    <div className={`codex-info-row ${muted ? "muted" : ""}`}>
      <Icon size={17} />
      <span>{label}</span>
      {value && <strong title={String(value)}>{value}</strong>}
      {action}
    </div>
  );
}

function backendStatusLabel(status = {}) {
  const backend = String(status?.backend || "").toLowerCase();
  if (backend === "go" && status.healthy) return "Go · 已连接";
  if (backend === "go") return "Go · 未连接";
  if (status?.fallback_enabled || backend === "python") return "Python fallback";
  return "未启用";
}

function ProgressList({ state }) {
  const snapshot = sessionSnapshot(state);
  const tasks = hasConcreteRunContext(state) ? uniqueVisibleTasks(state.tasks || []) : [];
  const ordered = [...tasks].sort((a, b) => taskPriority(a) - taskPriority(b));
  const completedCount = tasks.filter((task) => ["completed", "skipped"].includes(task.status)).length;
  const isLeadDirect = snapshot?.run?.strategy === "lead_direct_reply" || state.currentRunStrategy === "lead_direct_reply";
  const currentAction = isLeadDirect
    ? state.status === "completed"
      ? "Lead 直接回复完成。"
      : "Lead 正在直接回复，不创建任务卡。"
    : snapshot?.activity?.current_action;

  if (!tasks.length) {
    return (
      <div className="codex-progress-empty">
        <Clock3 size={17} />
        <span>{currentAction || "发送需求后，这里会显示 Lead 拆解出的关键任务。"}</span>
      </div>
    );
  }

  return (
    <>
      <div className="codex-progress-list" aria-label="关键任务进度">
        {ordered.map((task, index) => (
          <article key={task.id || `${task.title}-${index}`} className={`codex-progress-row ${task.status || "pending"}`}>
            <TaskIcon status={task.status} />
            <div>
              <strong title={task.title}>{task.title}</strong>
              <span>
                {task.owner || "Lead"} · {statusLabel(task.status)}
              </span>
            </div>
          </article>
        ))}
      </div>
      <div className="codex-progress-summary">
        <span>{completedCount} / {tasks.length} 已完成</span>
        {state.status && <strong>{statusLabel(state.status)}</strong>}
      </div>
    </>
  );
}

function Environment({ state }) {
  const snapshot = sessionSnapshot(state);
  const workspace = snapshot?.workspace || {};
  const changes = snapshot?.changes || {};
  const filesChanged = snapshot
    ? Number(changes.files_changed ?? (Array.isArray(changes.files) ? changes.files.length : 0))
    : Number(state.metrics?.files ?? state.diffFiles?.length ?? 0);
  const insertions = snapshot ? Number(changes.insertions ?? 0) : Number(state.metrics?.insertions ?? 0);
  const deletions = snapshot ? Number(changes.deletions ?? 0) : Number(state.metrics?.deletions ?? 0);
  const branch = workspace.is_git_repo ? workspace.git_branch || "unknown" : "非 Git 工作区";
  const indexer = state.runtimeStatus?.indexer || {};
  const filetools = state.runtimeStatus?.filetools || {};
  const executor = state.runtimeStatus?.executor || {};
  const mcpGateway = state.runtimeStatus?.mcpGateway || {};
  const indexerMuted = !indexer.healthy || indexer.backend !== "go";
  const filetoolsMuted = !filetools.healthy || filetools.backend !== "go";
  const executorMuted = !executor.healthy || executor.backend !== "go";
  const mcpGatewayMuted = !mcpGateway.healthy || mcpGateway.backend !== "go";

  return (
    <Section title="环境信息" action={<Settings2 size={16} />}>
      <InfoRow
        icon={GitCompare}
        label="变更"
        value=""
        action={(
          <span className="codex-change-stats">
            <span>+{insertions}</span>
            <span>-{deletions}</span>
          </span>
        )}
      />
      <InfoRow icon={HardDrive} label="本地" value={shortPath(workspace.path || state.workspaceDir) || "未选择目录"} />
      <InfoRow icon={GitBranch} label={branch} value={workspace.dirty ? `${filesChanged} 个文件变更` : "干净"} />
      <InfoRow
        icon={Search}
        label="项目索引"
        value={backendStatusLabel(indexer)}
        muted={indexerMuted}
      />
      <InfoRow
        icon={Wrench}
        label="文件工具"
        value={backendStatusLabel(filetools)}
        muted={filetoolsMuted}
      />
      <InfoRow
        icon={Terminal}
        label="命令执行"
        value={backendStatusLabel(executor)}
        muted={executorMuted}
      />
      <InfoRow
        icon={Plug}
        label="MCP Gateway"
        value={backendStatusLabel(mcpGateway)}
        muted={mcpGatewayMuted}
      />
    </Section>
  );
}

function QualityInfo({ state }) {
  const snapshot = sessionSnapshot(state);
  const quality = snapshot?.quality || state.report?.quality || {};
  const risks = Array.isArray(quality.risks) && quality.risks.length ? quality.risks : state.report?.risks || [];
  const failed = Array.isArray(quality.gates) ? quality.gates.filter((gate) => gate.status === "failed").length : 0;

  return (
    <Section title="质量">
      <InfoRow
        icon={ShieldCheck}
        label={quality.score == null ? "质量门禁" : `${quality.score} / 100`}
        value={failed ? `${failed} 项失败` : risks.length ? `${risks.length} 个风险` : "就绪"}
        muted={!failed && !risks.length}
      />
    </Section>
  );
}

export default function RunInspector({ state }) {
  return (
    <div className="codex-inspector">
      <div className="codex-inspector-card">
        <Section title="进度">
          <ProgressList state={state} />
        </Section>
        <Environment state={state} />
        <QualityInfo state={state} />
      </div>
    </div>
  );
}
