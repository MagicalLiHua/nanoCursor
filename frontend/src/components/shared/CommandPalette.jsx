import React from "react";
import { Command } from "lucide-react";

export default function CommandPalette({ ui, commands, onClose, onRunCommand, onQueryChange }) {
  if (!ui?.commandPaletteOpen) return null;
  return (
    <div className="command-overlay" onClick={onClose}>
      <section className="command-palette" role="dialog" aria-modal="true" aria-label="命令面板" onClick={(e) => e.stopPropagation()}>
        <div className="command-input-wrap">
          <span className="command-shortcut"><Command size={14} /> K</span>
          <input
            id="command-input"
            value={ui.commandQuery || ""}
            onChange={(e) => onQueryChange?.(e.target.value)}
            placeholder="搜索命令、布局、报告、MCP..."
            autoComplete="off"
          />
          <button className="icon-button subtle" onClick={onClose} type="button" title="关闭命令面板">×</button>
        </div>
        <div className="command-list">
          {commands.length ? (
            commands.map((item) => (
              <button
                key={item.id}
                className="command-item"
                onClick={() => onRunCommand?.(item.id)}
                type="button"
              >
                <span className="command-section">{item.section}</span>
                <strong>{item.title}</strong>
                <small>{item.description}</small>
                <kbd>{item.shortcut}</kbd>
              </button>
            ))
          ) : (
            <div className="command-empty">没有匹配的命令</div>
          )}
        </div>
      </section>
    </div>
  );
}
