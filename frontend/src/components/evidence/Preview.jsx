import React from "react";

export default function Preview({ previewUrl = "" }) {
  if (!previewUrl) {
    return (
      <div className="preview-empty">
        <strong>尚未启动预览</strong>
        <span>运行任务后，如果后端提供预览地址，这里会显示可检查的前端页面。</span>
      </div>
    );
  }

  return (
    <div className="preview-frame">
      <div className="preview-surface">
        <div className="preview-top">
          <span className="browser-dot" />
          <span className="browser-dot" />
          <span className="browser-dot" />
          <span className="panel-subtitle">{previewUrl}</span>
        </div>
        <div className="preview-body">
          <span className="panel-subtitle">预览服务已就绪，可在后续接入 iframe 或外部浏览器打开。</span>
        </div>
      </div>
    </div>
  );
}
