import { escapeHtml } from "../core/format.js";

export function renderRecovery(center = {}) {
  const summary = center.summary || {};
  const points = center.recovery_points || [];
  const risks = center.risks || [];
  const actions = center.actions || [];
  const failureGroups = center.failure_groups || [];
  return `
    <div class="recovery-center">
      <section class="recovery-summary">
        <div class="recovery-status ${escapeHtml(center.status || "unknown")}">
          <strong>${recoveryStatusLabel(center.status)}</strong>
          <span>安全状态</span>
        </div>
        <div class="artifact-summary-grid">
          <div><strong>${escapeHtml(summary.snapshot_count ?? 0)}</strong><span>快照</span></div>
          <div><strong>${escapeHtml(summary.backup_count ?? 0)}</strong><span>备份</span></div>
          <div><strong>${escapeHtml(summary.risk_count ?? 0)}</strong><span>风险</span></div>
          <div><strong>${escapeHtml(summary.high_risk_count ?? 0)}</strong><span>高风险</span></div>
        </div>
      </section>
      <section class="recovery-action-panel">
        <div class="recovery-section-head">
          <h3>推荐修复路径</h3>
          <span>${escapeHtml(actions.length || 0)} 步</span>
        </div>
        <div class="recovery-action-list">
          ${actions.length ? actions.map(renderRecoveryAction).join("") : `<div class="recovery-ok">暂无需要处理的恢复动作</div>`}
        </div>
      </section>
      <section class="recovery-grid">
        <div>
          <h3>恢复点</h3>
          <div class="recovery-list">
            ${points.length ? points.map(renderRecoveryPoint).join("") : `<div class="empty-mini">暂无快照或备份</div>`}
          </div>
        </div>
        <div>
          <h3>风险和诊断</h3>
          <div class="recovery-list">
            ${risks.length ? risks.map(renderRecoveryRisk).join("") : `<div class="recovery-ok">未发现阻塞风险</div>`}
          </div>
        </div>
      </section>
      ${
        failureGroups.length
          ? `
      <section class="recovery-failure-groups">
        <h3>失败原因分类</h3>
        <div class="failure-group-list">
          ${failureGroups
            .map(
              (group) => `
            <div class="failure-group-chip">
              <span class="badge ${escapeHtml(group.category)}">${escapeHtml(group.category)}</span>
              <strong>${escapeHtml(group.count)}</strong>
            </div>
          `,
            )
            .join("")}
        </div>
      </section>`
          : ""
      }
      <section class="remediation-panel">
        <h3>创建补救 Run</h3>
        <p>基于当前失败的 run 证据，自动生成修复提示并启动新的修复运行。</p>
        <input id="remediation-instruction" placeholder="补充修复指令（可选）" />
        <button class="button primary compact-button" data-action="create-remediation" type="button">创建补救 Run</button>
      </section>
    </div>
  `;
}

function renderRecoveryAction(action) {
  return `
    <article class="recovery-action ${escapeHtml(action.priority || "low")} ${action.enabled ? "" : "disabled"}">
      <div>
        <span>${escapeHtml(recoveryActionTypeLabel(action.action_type))}</span>
        <strong>${escapeHtml(action.title)}</strong>
        <p>${escapeHtml(action.detail || "")}</p>
      </div>
      ${action.enabled ? `<button class="button compact-button ${action.priority === "high" ? "primary" : "secondary"}" data-action="execute-recovery" data-action-id="${escapeHtml(action.id)}" data-target="${escapeHtml(action.target || "")}" data-target-path="${escapeHtml(action.target_path || "")}" type="button">执行</button>` : ""}
    </article>
  `;
}

function renderRecoveryPoint(point) {
  return `
    <article class="recovery-card">
      <div class="recovery-card-head">
        <span class="artifact-kind">${escapeHtml(point.kind)}</span>
        <span class="badge ${escapeHtml(point.status)}">${escapeHtml(point.status || "available")}</span>
      </div>
      <h4>${escapeHtml(point.label || point.id)}</h4>
      <p>${escapeHtml(point.detail || point.reason || "")}</p>
      <div class="artifact-meta">
        ${point.target_path ? `<span>${escapeHtml(point.target_path)}</span>` : ""}
        ${point.size ? `<span>${escapeHtml(point.size)} bytes</span>` : ""}
      </div>
    </article>
  `;
}

function renderRecoveryRisk(risk) {
  return `
    <article class="recovery-card risk-${escapeHtml(risk.severity)}">
      <div class="recovery-card-head">
        <span class="artifact-kind">${escapeHtml(risk.severity)}</span>
        <span class="badge ${escapeHtml(risk.severity)}">${riskSeverityLabel(risk.severity)}</span>
      </div>
      <h4>${escapeHtml(risk.title)}</h4>
      <p>${escapeHtml(risk.detail || "")}</p>
    </article>
  `;
}

function recoveryStatusLabel(status) {
  const labels = {
    safe: "安全",
    review: "需复核",
    attention: "需处理",
    unprotected: "未保护",
  };
  return labels[status] || status || "未知";
}

function riskSeverityLabel(severity) {
  const labels = {
    high: "高",
    medium: "中",
    low: "低",
  };
  return labels[severity] || severity || "未知";
}

function recoveryActionTypeLabel(actionType) {
  const labels = {
    inspect_timeline: "时间线",
    review_diff: "Diff",
    quality_gate: "质量",
    recovery_point: "恢复点",
    snapshot: "快照",
    continue: "继续",
  };
  return labels[actionType] || actionType || "动作";
}
