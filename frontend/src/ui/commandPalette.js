import { escapeHtml } from "../core/format.js";

export function renderCommandPalette({ ui, commands }) {
  if (!ui?.commandPaletteOpen) return "";
  return `
    <div class="command-overlay" data-action="close-command-palette">
      <section class="command-palette" role="dialog" aria-modal="true" aria-label="命令面板">
        <div class="command-input-wrap">
          <span>⌘K</span>
          <input id="command-input" value="${escapeHtml(ui.commandQuery || "")}" placeholder="搜索命令、布局、报告、MCP..." autocomplete="off" />
          <button class="icon-button subtle" data-action="close-command-palette" type="button" title="关闭命令面板">×</button>
        </div>
        <div class="command-list">
          ${
            commands.length
              ? commands
                  .map(
                    (item) => `
                      <button class="command-item" data-action="run-command" data-command-id="${escapeHtml(item.id)}" type="button">
                        <span class="command-section">${escapeHtml(item.section)}</span>
                        <strong>${escapeHtml(item.title)}</strong>
                        <small>${escapeHtml(item.description)}</small>
                        <kbd>${escapeHtml(item.shortcut)}</kbd>
                      </button>
                    `,
                  )
                  .join("")
              : `<div class="command-empty">没有匹配的命令</div>`
          }
        </div>
      </section>
    </div>
  `;
}
