import React from "react";

export default function Metrics({ state }) {
  const metrics = [
    ["任务", state.metrics.tasks],
    ["文件", state.metrics.files],
    ["工具调用", state.metrics.toolCalls],
    ["Token", state.metrics.tokens],
    ["验证", state.metrics.tests],
  ];

  return (
    <div className="metric-list">
      {metrics.map(([label, value]) => (
        <div key={label} className="metric-item">
          <span className="metric-label">{label}</span>
          <span className="metric-value">{value}</span>
        </div>
      ))}
    </div>
  );
}
