import React from "react";
import { shortPath } from "../../core/format.js";
import { renderMarkdown } from "../../core/markdown.js";

const DELIVERY_LEVEL_LABELS = { excellent: "优秀", good: "良好", acceptable: "可接受", weak: "需改进" };
const QUALITY_STATUS_LABELS = { passed: "通过", warning: "提醒", failed: "失败" };
const TRACEABILITY_STATUS_LABELS = { covered: "已覆盖", partial: "部分覆盖", missing: "未覆盖" };

function markdownSection(markdown, heading) {
  if (!markdown) return [];
  const lines = markdown.split("\n");
  const start = lines.findIndex((l) => l.trim().toLowerCase() === `## ${heading}`.toLowerCase());
  if (start < 0) return [];
  const items = [];
  for (const line of lines.slice(start + 1)) {
    if (line.startsWith("## ")) break;
    const clean = line.replace(/^[-*]\s*/, "").replace(/^#+\s*/, "").trim();
    if (clean) items.push(clean);
  }
  return items;
}

function textFromEvidence(item) {
  if (!item) return "";
  if (typeof item === "string") return item;
  return item.title || item.label || item.description || item.message || item.path || JSON.stringify(item);
}

function dedupeTextItems(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = typeof item === "string" ? item : JSON.stringify(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function ReportKpi({ label, value, detail }) {
  return (
    <div className="report-kpi">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function ReportSection({ title, items, renderItem, emptyText }) {
  return (
    <section className="report-card">
      <div className="report-card-head">
        <h4>{title}</h4>
        <span className="panel-subtitle">{items.length} 项</span>
      </div>
      <div className="report-evidence-list">
        {items.length ? (
          items.slice(0, 8).map((item, i) => <React.Fragment key={i}>{renderItem(item)}</React.Fragment>)
        ) : (
          <div className="empty-mini">{emptyText}</div>
        )}
      </div>
    </section>
  );
}

function ChangedFileEvidence({ file }) {
  return (
    <div className="report-evidence">
      <strong>{shortPath(file.path)}</strong>
      <span>{file.change_type || file.status || "modified"}</span>
    </div>
  );
}

function TextEvidence({ item }) {
  return (
    <div className="report-evidence text-only">
      <span>{textFromEvidence(item)}</span>
    </div>
  );
}

function QualityEvidence({ quality }) {
  const checks = quality?.checks || [];
  return (
    <section className="report-card">
      <div className="report-card-head">
        <h4>质量门禁</h4>
        <span className={`badge ${quality?.status || "unknown"}`}>
          {QUALITY_STATUS_LABELS[quality?.status] || quality?.status || "未知"}
        </span>
      </div>
      <div className="quality-summary">
        <div><strong>{quality?.passed_count ?? 0}</strong><span>通过</span></div>
        <div><strong>{quality?.warning_count ?? 0}</strong><span>提醒</span></div>
        <div><strong>{quality?.failed_count ?? 0}</strong><span>失败</span></div>
      </div>
      <div className="report-evidence-list">
        {checks.length ? (
          checks.slice(0, 6).map((check, i) => (
            <div key={i} className="report-evidence">
              <strong>{check.label || check.id}</strong>
              <span>{QUALITY_STATUS_LABELS[check.status] || check.status || "未知"}</span>
            </div>
          ))
        ) : (
          <div className="empty-mini">暂无质量检查项</div>
        )}
      </div>
    </section>
  );
}

function Traceability({ traceability }) {
  if (!traceability?.requirements?.length) return null;
  const percent = Math.round((traceability.coverageRate || 0) * 100);
  return (
    <section className="traceability">
      <div className="traceability-head">
        <div>
          <h4>需求追踪矩阵</h4>
          <p>{traceability.coveredCount || 0} / {traceability.totalCount || 0} 个需求已覆盖</p>
        </div>
        <span className="score-chip">{percent}%</span>
      </div>
      <div className="traceability-list">
        {traceability.requirements.map((item, i) => (
          <article key={i} className="trace-row">
            <div className="trace-main">
              <div className="trace-title">{item.id} · {item.title}</div>
              <span className={`badge ${item.status}`}>
                {TRACEABILITY_STATUS_LABELS[item.status] || item.status || "未知"}
              </span>
            </div>
            <div className="trace-columns">
              <span>任务：{(item.tasks || []).join(", ") || "未关联"}</span>
              <span>文件：{(item.files || []).join(", ") || "未关联"}</span>
              <span>验证：{(item.tests || []).join(", ") || "未关联"}</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function RawReport({ markdown }) {
  return (
    <details className="raw-report">
      <summary>查看完整 Markdown 报告</summary>
      <div className="report-markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(markdown) }} />
    </details>
  );
}

export default function Report({ state }) {
  const outcomeReport = state.runOutcome?.report || {};
  const outcomeSummary = state.runOutcome?.summary || {};

  if (state.report.source === "not_applicable" || outcomeReport.source === "not_applicable") {
    return (
      <article className="report structured-report">
        <section className="report-hero compact">
          <div>
            <span className="artifact-kind">Delivery Report</span>
            <h3>无需交付报告</h3>
            <p>{outcomeReport.summary || outcomeSummary.final_message || state.report.summary || "这是一轮轻量对话，没有代码变更、工具调用或交付物。"}</p>
          </div>
        </section>
      </article>
    );
  }

  const getArtifact = (id) => (state.artifactCenter?.artifacts || []).find((a) => a.id === id);
  const getPayload = (id) => getArtifact(id)?.payload || null;

  const score = getPayload("score");
  const quality = state.runOutcome?.quality || state.report.quality || getPayload("quality");
  const outcomeTraceability = state.runOutcome?.traceability || {};
  const coverage = outcomeTraceability.coverage_rate || state.report.traceability?.coverageRate || state.artifactCenter?.summary?.coverage_rate || 0;
  const coveragePercent = Math.round(coverage * 100);
  const coveredCount = outcomeTraceability.covered_count ?? state.report.traceability?.coveredCount ?? 0;
  const totalCount = outcomeTraceability.total_count ?? state.report.traceability?.totalCount ?? 0;

  const outcomeFiles = state.runOutcome?.changes?.files || [];
  const artifactFiles = getPayload("changed_files")?.changed_files || [];
  const changedFiles = (outcomeFiles.length ? outcomeFiles : artifactFiles.length ? artifactFiles : state.report.changedFiles || [])
    .map((f) => (typeof f === "string" ? { path: f, change_type: "modified" } : f))
    .filter((f) => f?.path);

  const allRisks = [
    ...(state.runOutcome?.report?.risks || []),
    ...(state.runOutcome?.recovery?.risks || []),
    ...(getPayload("risks")?.risks || []),
    ...(state.report.risks || []),
  ].filter(Boolean);
  const riskItems = dedupeTextItems(allRisks);

  const outcomeSteps = state.runOutcome?.recovery?.actions || state.runOutcome?.report?.next_steps || [];
  const nextSteps = outcomeSteps.length
    ? outcomeSteps.map(textFromEvidence).filter(Boolean)
    : markdownSection(state.report.markdown, "Next Steps").length
      ? markdownSection(state.report.markdown, "Next Steps")
      : ["补充项目展示材料。", "继续增强报告结构化展示和 Diff 审查体验。"];

  const summaryText = (() => {
    if (outcomeReport.summary) return outcomeReport.summary;
    if (state.runOutcome?.summary?.final_message) return state.runOutcome.summary.final_message;
    if (state.report.summary && state.report.summary !== "Loaded saved delivery report.") return state.report.summary;
    const fromMd = markdownSection(state.report.markdown, "Summary");
    if (fromMd.length) return fromMd[0];
    return "本次运行已归档需求、任务、变更文件、质量门禁、风险和交付报告。";
  })();

  return (
    <article className="report structured-report">
      <section className="report-hero">
        <div>
          <span className="artifact-kind">Delivery Report</span>
          <h3>交付证据总览</h3>
          <p>{summaryText}</p>
        </div>
        <div className="report-hero-actions">
          {state.report.markdown && (
            <button className="button secondary compact-button" data-action="copy-report-md" type="button">复制 Markdown</button>
          )}
          <div className="report-score">
            <strong>{score?.score ?? state.artifactCenter?.summary?.score ?? "--"}</strong>
            <span>{DELIVERY_LEVEL_LABELS[score?.level] || score?.level || "交付评分"}</span>
          </div>
        </div>
      </section>

      <section className="report-kpis">
        <ReportKpi label="需求覆盖" value={`${coveragePercent}%`} detail={`${coveredCount} / ${totalCount}`} />
        <ReportKpi label="质量门禁" value={QUALITY_STATUS_LABELS[quality?.status] || quality?.status || "未知"} detail={`${quality?.passed_count ?? 0} 通过 · ${quality?.warning_count ?? 0} 提醒`} />
        <ReportKpi label="变更文件" value={changedFiles.length} detail={changedFiles.length ? "已生成 Diff" : "暂无文件变更"} />
        <ReportKpi label="风险" value={riskItems.length} detail={riskItems.length ? "需要复核" : "未发现阻塞风险"} />
      </section>

      <section className="report-grid">
        <ReportSection title="变更文件" items={changedFiles} renderItem={(f) => <ChangedFileEvidence file={f} />} emptyText="暂无变更文件" />
        <QualityEvidence quality={quality} />
        <ReportSection title="风险与下一步" items={riskItems.length ? riskItems : nextSteps} renderItem={(item) => <TextEvidence item={item} />} emptyText="未发现阻塞风险" />
      </section>

      <Traceability traceability={state.report.traceability} />
      {state.report.markdown && <RawReport markdown={state.report.markdown} />}
    </article>
  );
}
