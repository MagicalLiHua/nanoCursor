export function buildReportText(report) {
  return [
    "# 交付报告",
    "",
    report.summary,
    "",
    "## 验收点",
    ...report.requirements.map((item) => `- ${item}`),
    "",
    "## 需求追踪",
    ...(report.traceability?.requirements || []).map(
      (item) => `- ${item.id} ${item.title}: ${traceabilityStatusLabel(item.status)}`,
    ),
    "",
    "## 变更文件",
    ...report.changedFiles.map((item) => `- ${item}`),
    "",
    "## 风险和下一步",
    ...report.risks.map((item) => `- ${item}`),
  ].join("\n");
}

function traceabilityStatusLabel(status) {
  const labels = {
    covered: "已覆盖",
    partial: "部分覆盖",
    missing: "未覆盖",
  };
  return labels[status] || status || "未知";
}
