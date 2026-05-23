import { escapeHtml, shortPath } from "../core/format.js";

let viewState = null;

export function renderReport(state) {
  viewState = state;
  return renderReportBody();
}

function renderReportBody() {
  const score = getArtifactPayload("score");
  const quality = getArtifactPayload("quality");
  const riskItems = collectReportRisks();
  const changedFiles = collectChangedFiles();
  const nextSteps = collectNextSteps();
  const summary = reportSummaryText();
  const coverage = viewState.report.traceability?.coverageRate || viewState.artifactCenter?.summary?.coverage_rate || 0;
  const coveragePercent = Math.round(coverage * 100);

  return `
    <article class="report structured-report">
      <section class="report-hero">
        <div>
          <span class="artifact-kind">Delivery Report</span>
          <h3>交付证据总览</h3>
          <p>${escapeHtml(summary)}</p>
        </div>
        <div class="report-score">
          <strong>${escapeHtml(score?.score ?? viewState.artifactCenter?.summary?.score ?? "--")}</strong>
          <span>${escapeHtml(deliveryLevelLabel(score?.level))}</span>
        </div>
      </section>

      <section class="report-kpis">
        ${renderReportKpi("需求覆盖", `${coveragePercent}%`, `${viewState.report.traceability?.coveredCount || 0} / ${viewState.report.traceability?.totalCount || 0}`)}
        ${renderReportKpi("质量门禁", qualityStatusLabel(quality?.status), `${quality?.passed_count ?? 0} 通过 · ${quality?.warning_count ?? 0} 提醒`)}
        ${renderReportKpi("变更文件", changedFiles.length, changedFiles.length ? "已生成 Diff" : "暂无文件变更")}
        ${renderReportKpi("风险", riskItems.length, riskItems.length ? "需要复核" : "未发现阻塞风险")}
      </section>

      <section class="report-grid">
        ${renderReportSection("变更文件", changedFiles, renderChangedFileEvidence, "暂无变更文件")}
        ${renderQualityEvidence(quality)}
        ${renderReportSection("风险与下一步", riskItems.length ? riskItems : nextSteps, renderTextEvidence, "未发现阻塞风险")}
      </section>

      ${renderTraceability()}
      ${viewState.report.markdown ? renderRawReport(viewState.report.markdown) : ""}
    </article>
  `;
}

function getArtifact(id) {
  return (viewState.artifactCenter?.artifacts || []).find((item) => item.id === id);
}

function getArtifactPayload(id) {
  return getArtifact(id)?.payload || null;
}

function renderReportKpi(label, value, detail) {
  return `
    <div class="report-kpi">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `;
}

function reportSummaryText() {
  if (viewState.report.summary && viewState.report.summary !== "Loaded saved delivery report.") {
    return viewState.report.summary;
  }
  const fromMarkdown = markdownSection(viewState.report.markdown, "Summary");
  if (fromMarkdown.length) return fromMarkdown[0];
  return "本次运行已归档需求、任务、变更文件、质量门禁、风险和交付报告。";
}

function collectChangedFiles() {
  const artifactFiles = getArtifactPayload("changed_files")?.changed_files || [];
  const files = artifactFiles.length ? artifactFiles : viewState.report.changedFiles;
  return files
    .map((file) => (typeof file === "string" ? { path: file, change_type: "modified" } : file))
    .filter((file) => file?.path);
}

function collectReportRisks() {
  const artifactRisks = getArtifactPayload("risks")?.risks || [];
  const reportRisks = viewState.report.risks || [];
  return [...artifactRisks, ...reportRisks].filter(Boolean);
}

function collectNextSteps() {
  const steps = markdownSection(viewState.report.markdown, "Next Steps");
  return steps.length ? steps : ["补充项目展示材料。", "继续增强报告结构化展示和 Diff 审查体验。"];
}

function markdownSection(markdown, heading) {
  if (!markdown) return [];
  const lines = markdown.split("\n");
  const start = lines.findIndex((line) => line.trim().toLowerCase() === `## ${heading}`.toLowerCase());
  if (start < 0) return [];
  const items = [];
  for (const line of lines.slice(start + 1)) {
    if (line.startsWith("## ")) break;
    const clean = line.replace(/^[-*]\s*/, "").replace(/^#+\s*/, "").trim();
    if (clean) items.push(clean);
  }
  return items;
}

function renderReportSection(title, items, renderer, emptyText) {
  return `
    <section class="report-card">
      <div class="report-card-head">
        <h4>${escapeHtml(title)}</h4>
        <span class="panel-subtitle">${escapeHtml(items.length)} 项</span>
      </div>
      <div class="report-evidence-list">
        ${items.length ? items.slice(0, 8).map(renderer).join("") : `<div class="empty-mini">${escapeHtml(emptyText)}</div>`}
      </div>
    </section>
  `;
}

function renderChangedFileEvidence(file) {
  return `
    <div class="report-evidence">
      <strong>${escapeHtml(shortPath(file.path))}</strong>
      <span>${escapeHtml(file.change_type || file.status || "modified")}</span>
    </div>
  `;
}

function renderTextEvidence(item) {
  return `
    <div class="report-evidence text-only">
      <span>${escapeHtml(item)}</span>
    </div>
  `;
}

function renderQualityEvidence(quality) {
  const checks = quality?.checks || [];
  return `
    <section class="report-card">
      <div class="report-card-head">
        <h4>质量门禁</h4>
        <span class="badge ${escapeHtml(quality?.status || "unknown")}">${qualityStatusLabel(quality?.status)}</span>
      </div>
      <div class="quality-summary">
        <div><strong>${escapeHtml(quality?.passed_count ?? 0)}</strong><span>通过</span></div>
        <div><strong>${escapeHtml(quality?.warning_count ?? 0)}</strong><span>提醒</span></div>
        <div><strong>${escapeHtml(quality?.failed_count ?? 0)}</strong><span>失败</span></div>
      </div>
      <div class="report-evidence-list">
        ${
          checks.length
            ? checks
                .slice(0, 6)
                .map(
                  (check) => `
                    <div class="report-evidence">
                      <strong>${escapeHtml(check.label || check.id)}</strong>
                      <span>${escapeHtml(qualityCheckStatusLabel(check.status))}</span>
                    </div>
                  `,
                )
                .join("")
            : `<div class="empty-mini">暂无质量检查项</div>`
        }
      </div>
    </section>
  `;
}

function renderRawReport(markdown) {
  return `
    <details class="raw-report">
      <summary>查看原始 Markdown 报告</summary>
      <pre class="report-markdown">${escapeHtml(markdown)}</pre>
    </details>
  `;
}

function deliveryLevelLabel(level) {
  const labels = {
    excellent: "优秀",
    good: "良好",
    acceptable: "可接受",
    weak: "需改进",
  };
  return labels[level] || level || "交付评分";
}

function qualityStatusLabel(status) {
  const labels = {
    passed: "通过",
    warning: "提醒",
    failed: "失败",
  };
  return labels[status] || status || "未知";
}

function qualityCheckStatusLabel(status) {
  const labels = {
    passed: "通过",
    warning: "提醒",
    failed: "失败",
  };
  return labels[status] || status || "未知";
}

function renderTraceability() {
  const traceability = viewState.report.traceability;
  if (!traceability?.requirements?.length) {
    return "";
  }

  const percent = Math.round((traceability.coverageRate || 0) * 100);
  return `
    <section class="traceability">
      <div class="traceability-head">
        <div>
          <h4>需求追踪矩阵</h4>
          <p>${escapeHtml(traceability.coveredCount || 0)} / ${escapeHtml(traceability.totalCount || 0)} 个需求已覆盖</p>
        </div>
        <span class="score-chip">${escapeHtml(percent)}%</span>
      </div>
      <div class="traceability-list">
        ${traceability.requirements.map(renderTraceabilityItem).join("")}
      </div>
    </section>
  `;
}

function renderTraceabilityItem(item) {
  return `
    <article class="trace-row">
      <div class="trace-main">
        <div class="trace-title">${escapeHtml(item.id)} · ${escapeHtml(item.title)}</div>
        <span class="badge ${escapeHtml(item.status)}">${traceabilityStatusLabel(item.status)}</span>
      </div>
      <div class="trace-columns">
        <span>任务：${escapeHtml((item.tasks || []).join(", ") || "未关联")}</span>
        <span>文件：${escapeHtml((item.files || []).join(", ") || "未关联")}</span>
        <span>验证：${escapeHtml((item.tests || []).join(", ") || "未关联")}</span>
      </div>
    </article>
  `;
}

function traceabilityStatusLabel(status) {
  const labels = {
    covered: "已覆盖",
    partial: "部分覆盖",
    missing: "未覆盖",
  };
  return labels[status] || status || "未知";
}
