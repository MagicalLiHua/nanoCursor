import { escapeHtml } from "../core/format.js";

export function renderToast(toast) {
  if (!toast) return "";
  return `
    <div class="toast ${escapeHtml(toast.kind || "info")}" role="status">
      <strong>${escapeHtml(toast.title || "")}</strong>
      ${toast.content ? `<span>${escapeHtml(toast.content)}</span>` : ""}
    </div>
  `;
}
