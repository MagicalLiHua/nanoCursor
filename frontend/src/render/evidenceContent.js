import { escapeHtml, replayStatusLabel, shortPath } from "../core/format.js";

export function renderDiffView({ state, syncDiffFiles }) {
  syncDiffFiles();
  const files = state.diffFiles || [];
  const selected = files.find((file) => file.path === state.selectedDiffFile) || files[0];
  if (!files.length) {
    return `<div class="empty">暂无 Diff 记录</div>`;
  }

  return `
    <div class="diff-browser">
      <aside class="diff-file-list">
        <div class="diff-file-list-head">
          <strong>${escapeHtml(files.length)}</strong>
          <span>变更文件</span>
        </div>
        ${files.map((file) => renderDiffFileButton(file, state.selectedDiffFile)).join("")}
      </aside>
      <section class="diff-detail">
        <header class="diff-detail-head">
          <div>
            <span class="artifact-kind">${escapeHtml(selected.changeType || "modified")}</span>
            <h3 title="${escapeHtml(selected.path)}">${escapeHtml(shortPath(selected.path))}</h3>
          </div>
          <div class="diff-stats">
            <span class="diff-add">+${escapeHtml(selected.additions || 0)}</span>
            <span class="diff-del">-${escapeHtml(selected.deletions || 0)}</span>
          </div>
        </header>
        <pre class="diff-view">${escapeHtml(selected.diff || "该文件暂无可展示的 Diff 片段。")}</pre>
      </section>
    </div>
  `;
}

function renderDiffFileButton(file, selectedDiffFile) {
  const active = file.path === selectedDiffFile ? "active" : "";
  return `
    <button class="diff-file-item ${active}" data-action="select-diff-file" data-path="${escapeHtml(file.path)}">
      <span class="diff-file-name" title="${escapeHtml(file.path)}">${escapeHtml(shortPath(file.path))}</span>
      <span class="diff-file-meta">
        <span class="diff-add">+${escapeHtml(file.additions || 0)}</span>
        <span class="diff-del">-${escapeHtml(file.deletions || 0)}</span>
      </span>
    </button>
  `;
}

export function renderTimeline({ state, eventKind, renderEventCapabilityTrace }) {
  const replayControls = renderReplayControls(state.replay || {});
  const timelineBody = state.events.length
    ? `
      <div class="timeline">
        ${state.events
          .map(
            (event) => `
              <article class="event-item ${eventKind(event.type)}">
                <span class="event-line"></span>
                <div>
                  <div class="event-title">${escapeHtml(event.title || event.type)}</div>
                  <div class="event-content">${escapeHtml(event.content || "")}</div>
                  ${renderEventCapabilityTrace(event)}
                </div>
                <time class="event-time">${escapeHtml(event.time || "")}</time>
              </article>
            `,
          )
          .join("")}
      </div>
    `
    : `<div class="empty">等待事件流</div>`;

  return `
    <div class="timeline-shell">
      ${replayControls}
      ${timelineBody}
    </div>
  `;
}

function renderReplayControls(replay) {
  const total = replay.events?.length || 0;
  const index = Math.min(replay.index || 0, total);
  const percent = total ? Math.round((index / total) * 100) : 0;
  const canReplay = total > 0;
  const isPlaying = replay.status === "playing";
  const playLabel = index >= total ? "重放" : "播放";

  return `
    <div class="replay-bar">
      <div class="replay-status">
        <strong>${replayStatusLabel(replay.status)}</strong>
        <span>${escapeHtml(index)} / ${escapeHtml(total)} 事件</span>
      </div>
      <div class="replay-progress" aria-hidden="true">
        <span style="width: ${escapeHtml(percent)}%"></span>
      </div>
      <div class="replay-actions">
        <button class="button secondary" data-action="replay-play" ${!canReplay || isPlaying ? "disabled" : ""}>${playLabel}</button>
        <button class="button secondary" data-action="replay-pause" ${!isPlaying ? "disabled" : ""}>暂停</button>
        <button class="button secondary" data-action="replay-reset" ${!canReplay ? "disabled" : ""}>复位</button>
        <label class="replay-speed">
          <span>速度</span>
          <select data-action="replay-speed" ${!canReplay ? "disabled" : ""}>
            ${[0.5, 1, 2, 4]
              .map(
                (speed) =>
                  `<option value="${speed}" ${Number(replay.speed || 1) === speed ? "selected" : ""}>${speed}x</option>`,
              )
              .join("")}
          </select>
        </label>
      </div>
    </div>
  `;
}

export function renderPreview(previewUrl = "localhost:5173/demo-todo") {
  return `
    <div class="preview-frame">
      <div class="preview-surface">
        <div class="preview-top">
          <span class="browser-dot"></span>
          <span class="browser-dot"></span>
          <span class="browser-dot"></span>
          <span class="panel-subtitle">${escapeHtml(previewUrl)}</span>
        </div>
        <div class="preview-body">
          <input class="preview-input" value="搜索任务：localStorage" readonly />
          <div class="preview-list">
            <div class="preview-row"><span class="badge completed">完成</span><span>新增 Todo 输入框</span><button class="button secondary">删除</button></div>
            <div class="preview-row"><span class="badge completed">完成</span><span>保存到 localStorage</span><button class="button secondary">删除</button></div>
            <div class="preview-row"><span class="badge pending">待处理</span><span>补充自动化测试</span><button class="button secondary">删除</button></div>
          </div>
        </div>
      </div>
    </div>
  `;
}
