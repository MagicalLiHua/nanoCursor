export function parseFrontmatter(raw = "") {
  if (!raw.startsWith("---\n")) {
    return { attributes: {}, body: raw };
  }
  const end = raw.indexOf("\n---", 4);
  if (end < 0) return { attributes: {}, body: raw };

  const block = raw.slice(4, end).trim();
  const body = raw.slice(end + 4).replace(/^\n/, "");
  const attributes = {};
  let currentKey = "";

  block.split(/\r?\n/).forEach((line) => {
    if (!line.trim()) return;
    if (/^\s+-\s+/.test(line) && currentKey) {
      attributes[currentKey] = Array.isArray(attributes[currentKey])
        ? [...attributes[currentKey], line.replace(/^\s+-\s+/, "").trim()]
        : [line.replace(/^\s+-\s+/, "").trim()];
      return;
    }
    const match = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (!match) return;
    currentKey = match[1];
    const value = match[2].trim();
    attributes[currentKey] = value || [];
  });

  return { attributes, body };
}
