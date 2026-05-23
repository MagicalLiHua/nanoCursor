import { escapeHtml, shortPath } from "../core/format.js";

export function renderArtifacts(center) {
  const artifacts = center?.artifacts || [];
  if (!artifacts.length) {
    return `<div class="empty">暂无交付物索引</div>`;
  }

  const summary = center.summary || {};
  return `
    <div class="artifact-center">
      <section class="artifact-summary">
        <div class="artifact-score">
          <span>${escapeHtml(summary.score ?? "--")}</span>
          <small>交付评分</small>
        </div>
        <div class="artifact-summary-grid">
          <div><strong>${escapeHtml(summary.artifact_count ?? artifacts.length)}</strong><span>交付物</span></div>
          <div><strong>${escapeHtml(summary.ready_count ?? 0)}</strong><span>就绪</span></div>
          <div><strong>${escapeHtml(summary.warning_count ?? 0)}</strong><span>提醒</span></div>
          <div><strong>${escapeHtml(Math.round((summary.coverage_rate || 0) * 100))}%</strong><span>需求覆盖</span></div>
        </div>
      </section>
      <section class="artifact-grid">
        ${artifacts.map(renderArtifactCard).join("")}
      </section>
    </div>
  `;
}

function renderArtifactCard(item) {
  return `
    <article class="artifact-card">
      <div class="artifact-card-head">
        <span class="artifact-kind">${escapeHtml(item.kind)}</span>
        <span class="badge ${escapeHtml(item.status)}">${artifactStatusLabel(item.status)}</span>
      </div>
      <h3>${escapeHtml(item.label)}</h3>
      <p>${escapeHtml(item.summary || "")}</p>
      <div class="artifact-meta">
        ${item.count === null || item.count === undefined ? "" : `<span>数量 ${escapeHtml(item.count)}</span>`}
        ${item.path ? `<span title="${escapeHtml(item.path)}">${escapeHtml(shortPath(item.path))}</span>` : ""}
      </div>
    </article>
  `;
}

function artifactStatusLabel(status) {
  const labels = {
    ready: "就绪",
    warning: "提醒",
    missing: "缺失",
    empty: "暂无",
    incomplete: "未完整",
  };
  return labels[status] || status || "未知";
}
