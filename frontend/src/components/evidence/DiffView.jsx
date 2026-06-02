import React from "react";
import { shortPath } from "../../core/format.js";
import EmptyState from "./EmptyState.jsx";

function highlightDiff(diff) {
  return String(diff)
    .split("\n")
    .map((line) => {
      if (line.startsWith("@@")) return `<span class="diff-hl-hunk">${line}</span>`;
      if (line.startsWith("+++") || line.startsWith("---")) return `<span class="diff-hl-meta">${line}</span>`;
      if (line.startsWith("+")) return `<span class="diff-hl-add">${line}</span>`;
      if (line.startsWith("-")) return `<span class="diff-hl-del">${line}</span>`;
      if (line.startsWith("diff --git")) return `<span class="diff-hl-header">${line}</span>`;
      return line;
    })
    .join("\n");
}

function DiffFileButton({ file, isSelected, onSelect }) {
  return (
    <button
      className={`diff-file-item ${isSelected ? "active" : ""}`}
      onClick={() => onSelect(file.path)}
    >
      <span className="diff-file-name" title={file.path}>{shortPath(file.path)}</span>
      <span className="diff-file-meta">
        <span className="diff-add">+{file.additions || 0}</span>
        <span className="diff-del">-{file.deletions || 0}</span>
      </span>
    </button>
  );
}

export default function DiffView({ state, onSelectFile }) {
  const files = state.diffFiles || [];
  const selected = files.find((f) => f.path === state.selectedDiffFile) || files[0];

  if (!files.length) {
    return (
      <EmptyState
        title="暂无 Diff"
        detail="当前运行还没有可展示的文件变更。"
      />
    );
  }

  return (
    <div className="diff-browser">
      <aside className="diff-file-list">
        <div className="diff-file-list-head">
          <strong>{files.length}</strong>
          <span>变更文件</span>
        </div>
        {files.map((file) => (
          <DiffFileButton
            key={file.path}
            file={file}
            isSelected={file.path === (state.selectedDiffFile || files[0]?.path)}
            onSelect={onSelectFile}
          />
        ))}
      </aside>
      <section className="diff-detail">
        <header className="diff-detail-head">
          <div>
            <span className="artifact-kind">{selected.changeType || "modified"}</span>
            <h3 title={selected.path}>{shortPath(selected.path)}</h3>
          </div>
          <div className="diff-stats">
            <span className="diff-add">+{selected.additions || 0}</span>
            <span className="diff-del">-{selected.deletions || 0}</span>
          </div>
        </header>
        <pre
          className="diff-view"
          dangerouslySetInnerHTML={{
            __html: selected.diff ? highlightDiff(selected.diff) : "该文件暂无可展示的 Diff 片段。",
          }}
        />
      </section>
    </div>
  );
}
