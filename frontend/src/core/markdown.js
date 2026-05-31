import { escapeHtml } from "./format.js";

export function renderMarkdown(markdown = "") {
  const text = String(markdown || "").replace(/\r\n/g, "\n").trim();
  if (!text) return "";

  const lines = text.split("\n");
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const language = trimmed.slice(3).trim();
      const code = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        code.push(lines[i]);
        i += 1;
      }
      i += i < lines.length ? 1 : 0;
      const langLabel = language ? `<span class="code-lang">${escapeHtml(language)}</span>` : "";
      blocks.push(
        `<div class="code-block">${langLabel}<pre class="md-code"><code${language ? ` data-lang="${escapeHtml(language)}"` : ""}>${escapeHtml(code.join("\n"))}</code></pre></div>`,
      );
      continue;
    }

    if (isTableStart(lines, i)) {
      const tableLines = [lines[i], lines[i + 1]];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        tableLines.push(lines[i]);
        i += 1;
      }
      blocks.push(renderTable(tableLines));
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length + 2;
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*]\s+/, ""));
        i += 1;
      }
      blocks.push(`<ul>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push(`<ol>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</ol>`);
      continue;
    }

    const paragraph = [trimmed];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith("```") &&
      !lines[i].trim().match(/^(#{1,4})\s+/) &&
      !/^[-*]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim()) &&
      !isTableStart(lines, i)
    ) {
      paragraph.push(lines[i].trim());
      i += 1;
    }
    blocks.push(`<p>${renderInline(paragraph.join(" "))}</p>`);
  }

  return `<div class="markdown-body">${blocks.join("")}</div>`;
}

function isTableStart(lines, index) {
  const head = lines[index]?.trim() || "";
  const divider = lines[index + 1]?.trim() || "";
  return head.startsWith("|") && divider.startsWith("|") && /^[\s|:-]+$/.test(divider);
}

function renderTable(lines) {
  const [head, , ...rows] = lines;
  const headers = splitTableRow(head);
  return `
    <div class="md-table-wrap">
      <table>
        <thead><tr>${headers.map((cell) => `<th>${renderInline(cell)}</th>`).join("")}</tr></thead>
        <tbody>
          ${rows
            .map((row) => `<tr>${splitTableRow(row).map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`)
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function splitTableRow(row) {
  return row
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderInline(raw = "") {
  let html = escapeHtml(raw);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  return html;
}
