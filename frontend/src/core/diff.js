export function normalizeChangedFile(file) {
  if (typeof file === "string") {
    return { path: file, change_type: "modified" };
  }
  return {
    path: file?.path || file?.new_path || file?.old_path || "",
    status: file?.status || "",
    change_type: file?.change_type || file?.changeType || "modified",
  };
}

export function stripDiffPathPrefix(path) {
  return String(path || "").replace(/^"|"$/g, "").replace(/^[ab]\//, "");
}

export function parseDiffHeader(line) {
  const match = line.match(/^diff --git (.+?) (.+)$/);
  if (!match) return null;
  const oldPath = stripDiffPathPrefix(match[1]);
  const newPath = stripDiffPathPrefix(match[2]);
  return { oldPath, newPath, path: newPath || oldPath };
}

export function parseUnifiedDiff(diff, changedFiles = []) {
  const normalizedFiles = changedFiles.map(normalizeChangedFile).filter((file) => file.path);
  const fileMeta = new Map(normalizedFiles.map((file) => [file.path, file]));
  const lines = String(diff || "").split("\n");
  const chunks = [];
  let current = null;

  function finishCurrent() {
    if (!current) return;
    current.diff = current.lines.join("\n");
    current.additions = current.lines.filter((line) => line.startsWith("+") && !line.startsWith("+++")).length;
    current.deletions = current.lines.filter((line) => line.startsWith("-") && !line.startsWith("---")).length;
    const meta = fileMeta.get(current.path) || {};
    current.changeType = meta.change_type || inferChangeType(current.lines);
    chunks.push(current);
  }

  for (const line of lines) {
    const header = parseDiffHeader(line);
    if (header) {
      finishCurrent();
      current = {
        path: header.path,
        oldPath: header.oldPath,
        lines: [line],
        additions: 0,
        deletions: 0,
        changeType: "modified",
        diff: "",
      };
      continue;
    }

    if (!current && line.trim()) {
      current = {
        path: normalizedFiles[0]?.path || "unified.diff",
        oldPath: normalizedFiles[0]?.path || "unified.diff",
        lines: [],
        additions: 0,
        deletions: 0,
        changeType: normalizedFiles[0]?.change_type || "modified",
        diff: "",
      };
    }

    if (current) {
      current.lines.push(line);
      if (line.startsWith("+++ ")) {
        const path = stripDiffPathPrefix(line.slice(4).trim());
        if (path && path !== "/dev/null") current.path = path;
      }
    }
  }
  finishCurrent();

  const seen = new Set(chunks.map((chunk) => chunk.path));
  normalizedFiles.forEach((file) => {
    if (!seen.has(file.path)) {
      chunks.push({
        path: file.path,
        oldPath: file.path,
        additions: 0,
        deletions: 0,
        changeType: file.change_type,
        diff: "",
      });
    }
  });

  return chunks;
}

export function inferChangeType(lines) {
  if (lines.some((line) => line.startsWith("new file mode"))) return "created";
  if (lines.some((line) => line.startsWith("deleted file mode"))) return "deleted";
  return "modified";
}
