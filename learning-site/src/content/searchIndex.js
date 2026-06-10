function normalize(value = "") {
  return String(value).toLowerCase();
}

function excerpt(text = "", query = "") {
  const normalized = normalize(text);
  const idx = normalized.indexOf(normalize(query));
  if (idx < 0) return text.slice(0, 150).trim();
  const start = Math.max(0, idx - 55);
  const end = Math.min(text.length, idx + query.length + 90);
  return `${start > 0 ? "..." : ""}${text.slice(start, end).trim()}${end < text.length ? "..." : ""}`;
}

export function searchDocuments(documents, query) {
  const q = query.trim();
  if (!q) return [];
  const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);

  return documents
    .map((doc) => {
      const haystack = normalize([
        doc.title,
        doc.groupLabel,
        doc.path,
        doc.headings.map((item) => item.text).join(" "),
        doc.body,
      ].join("\n"));
      const score = tokens.reduce((sum, token) => {
        if (!haystack.includes(token)) return sum;
        const titleHit = normalize(doc.title).includes(token) ? 5 : 0;
        const pathHit = normalize(doc.path).includes(token) ? 2 : 0;
        return sum + 1 + titleHit + pathHit;
      }, 0);
      return score > 0
        ? { doc, score, excerpt: excerpt(doc.body.replace(/\s+/g, " "), q) }
        : null;
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score || a.doc.order - b.doc.order)
    .slice(0, 20);
}
