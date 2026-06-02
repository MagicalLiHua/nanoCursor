import React from "react";
import { shortPath } from "../../core/format.js";
import EmptyState from "./EmptyState.jsx";

const STATUS_LABELS = {
  ready: "就绪",
  warning: "提醒",
  missing: "缺失",
  empty: "暂无",
  incomplete: "未完整",
};

function artifactStatusLabel(status) {
  return STATUS_LABELS[status] || status || "未知";
}

function ArtifactDetails({ item }) {
  if (item.id === "parallel_merge_plan") {
    const files = item.payload?.suggested_files || [];
    const stages = item.payload?.stage_guidance || [];
    if (!files.length && !stages.length) return null;
    return (
      <div className="artifact-proposal-preview">
        {stages.length ? (
          <div className="artifact-proposal-agents">
            {stages.slice(0, 4).map((stage, i) => (
              <span key={i}>{stage.stage_id || "stage"}</span>
            ))}
          </div>
        ) : null}
        {files.length ? (
          <div className="artifact-proposal-files">
            {files.slice(0, 4).map((file, i) => (
              <code key={i} title={file}>{shortPath(file)}</code>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  if (item.id !== "parallel_proposals") return null;
  const proposals = item.payload?.proposals || [];
  const files = item.payload?.summary?.suggested_files || [];
  if (!proposals.length && !files.length) return null;
  return (
    <div className="artifact-proposal-preview">
      {proposals.length ? (
        <div className="artifact-proposal-agents">
          {proposals.slice(0, 3).map((p, i) => (
            <span key={i}>{p.name || p.role || "Agent"}</span>
          ))}
        </div>
      ) : null}
      {files.length ? (
        <div className="artifact-proposal-files">
          {files.slice(0, 4).map((file, i) => (
            <code key={i} title={file}>{shortPath(file)}</code>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ArtifactCard({ item }) {
  const status = item.status || "unknown";
  return (
    <article className={`artifact-card status-${status}`}>
      <div className="artifact-card-head">
        <span className="artifact-kind">{item.kind}</span>
        <span className={`badge status-${status}`}>{artifactStatusLabel(item.status)}</span>
      </div>
      <h3>{item.label}</h3>
      <p>{item.summary || ""}</p>
      <ArtifactDetails item={item} />
      <div className="artifact-meta">
        {item.count != null && <span>数量 {item.count}</span>}
        {item.path && <span title={item.path}>{shortPath(item.path)}</span>}
      </div>
    </article>
  );
}

export default function Artifacts({ center }) {
  const artifacts = center?.artifacts || [];
  if (!artifacts.length) {
    return (
      <EmptyState
        title="暂无交付物"
        detail="运行完成后，报告、Diff、测试结果和恢复证据会汇总在这里。"
      />
    );
  }

  const summary = center.summary || {};
  return (
    <div className="artifact-center">
      <section className="artifact-summary">
        <div className="artifact-score">
          <span>{summary.score ?? "--"}</span>
          <small>交付评分</small>
        </div>
        <div className="artifact-summary-grid">
          <div><strong>{summary.artifact_count ?? artifacts.length}</strong><span>交付物</span></div>
          <div><strong>{summary.ready_count ?? 0}</strong><span>就绪</span></div>
          <div><strong>{summary.warning_count ?? 0}</strong><span>提醒</span></div>
          <div><strong>{Math.round((summary.coverage_rate || 0) * 100)}%</strong><span>需求覆盖</span></div>
        </div>
      </section>
      <section className="artifact-grid">
        {artifacts.map((item, i) => <ArtifactCard key={item.id || i} item={item} />)}
      </section>
    </div>
  );
}
