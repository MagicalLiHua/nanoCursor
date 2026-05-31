import React from "react";

const STATUS_LABELS = { safe: "安全", review: "需复核", attention: "需处理", unprotected: "未保护" };
const SEVERITY_LABELS = { high: "高", medium: "中", low: "低" };
const ACTION_TYPE_LABELS = {
  inspect_timeline: "时间线", review_diff: "Diff", quality_gate: "质量",
  recovery_point: "恢复点", snapshot: "快照", continue: "继续",
};

function RecoveryAction({ action }) {
  return (
    <article className={`recovery-action ${action.priority || "low"} ${action.enabled ? "" : "disabled"}`}>
      <div>
        <span>{ACTION_TYPE_LABELS[action.action_type] || action.action_type || "动作"}</span>
        <strong>{action.title}</strong>
        <p>{action.detail || ""}</p>
      </div>
      {action.enabled && (
        <button
          className={`button compact-button ${action.priority === "high" ? "primary" : "secondary"}`}
          data-action="execute-recovery"
          data-action-id={action.id}
          data-target={action.target || ""}
          data-target-path={action.target_path || ""}
          type="button"
        >
          执行
        </button>
      )}
    </article>
  );
}

function RecoveryPoint({ point }) {
  return (
    <article className="recovery-card">
      <div className="recovery-card-head">
        <span className="artifact-kind">{point.kind}</span>
        <span className={`badge ${point.status}`}>{point.status || "available"}</span>
      </div>
      <h4>{point.label || point.id}</h4>
      <p>{point.detail || point.reason || ""}</p>
      <div className="artifact-meta">
        {point.target_path && <span>{point.target_path}</span>}
        {point.size && <span>{point.size} bytes</span>}
      </div>
    </article>
  );
}

function RecoveryRisk({ risk }) {
  return (
    <article className={`recovery-card risk-${risk.severity}`}>
      <div className="recovery-card-head">
        <span className="artifact-kind">{risk.severity}</span>
        <span className={`badge ${risk.severity}`}>{SEVERITY_LABELS[risk.severity] || risk.severity || "未知"}</span>
      </div>
      <h4>{risk.title}</h4>
      <p>{risk.detail || ""}</p>
    </article>
  );
}

export default function Recovery({ center = {} }) {
  const summary = center.summary || {};
  const points = center.recovery_points || [];
  const risks = center.risks || [];
  const actions = center.actions || [];
  const failureGroups = center.failure_groups || [];

  return (
    <div className="recovery-center">
      <section className="recovery-summary">
        <div className={`recovery-status ${center.status || "unknown"}`}>
          <strong>{STATUS_LABELS[center.status] || center.status || "未知"}</strong>
          <span>安全状态</span>
        </div>
        <div className="artifact-summary-grid">
          <div><strong>{summary.snapshot_count ?? 0}</strong><span>快照</span></div>
          <div><strong>{summary.backup_count ?? 0}</strong><span>备份</span></div>
          <div><strong>{summary.risk_count ?? 0}</strong><span>风险</span></div>
          <div><strong>{summary.high_risk_count ?? 0}</strong><span>高风险</span></div>
        </div>
      </section>

      <section className="recovery-action-panel">
        <div className="recovery-section-head">
          <h3>推荐修复路径</h3>
          <span>{actions.length || 0} 步</span>
        </div>
        <div className="recovery-action-list">
          {actions.length ? (
            actions.map((a, i) => <RecoveryAction key={a.id || i} action={a} />)
          ) : (
            <div className="recovery-ok">暂无需要处理的恢复动作</div>
          )}
        </div>
      </section>

      <section className="recovery-grid">
        <div>
          <h3>恢复点</h3>
          <div className="recovery-list">
            {points.length ? (
              points.map((p, i) => <RecoveryPoint key={p.id || i} point={p} />)
            ) : (
              <div className="empty-mini">暂无快照或备份</div>
            )}
          </div>
        </div>
        <div>
          <h3>风险和诊断</h3>
          <div className="recovery-list">
            {risks.length ? (
              risks.map((r, i) => <RecoveryRisk key={i} risk={r} />)
            ) : (
              <div className="recovery-ok">未发现阻塞风险</div>
            )}
          </div>
        </div>
      </section>

      {failureGroups.length > 0 && (
        <section className="recovery-failure-groups">
          <h3>失败原因分类</h3>
          <div className="failure-group-list">
            {failureGroups.map((group, i) => (
              <div key={i} className="failure-group-chip">
                <span className={`badge ${group.category}`}>{group.category}</span>
                <strong>{group.count}</strong>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="remediation-panel">
        <h3>恢复运行</h3>
        <p>基于当前 run 的状态、失败阶段和错误证据，创建新的重试或补救运行。</p>
        <input id="retry-instruction" placeholder="补充重试指令（可选）" />
        <div className="remediation-actions">
          <button className="button secondary compact-button" data-action="retry-run" data-retry-mode="full" type="button">重试整轮</button>
          <button className="button secondary compact-button" data-action="retry-run" data-retry-mode="failed_stage" type="button">重试失败阶段</button>
          <button className="button primary compact-button" data-action="create-remediation" type="button">创建补救 Run</button>
        </div>
      </section>
    </div>
  );
}
