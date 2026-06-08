const MESSAGE_NOISE_PATTERNS = [
  /ev_poll_posix/i,
  /FD from fork parent still in poll list/i,
  /^\s*I\d{4}\s+\d{2}:\d{2}:\d{2}/,
  /grpc\._cython/i,
  /poller_completion_queue/i,
];

function stripPhaseMarkers(text) {
  return text
    .replace(/\s*#{1,6}\s*阶段\s*\d+\s*[：:]\s*/g, "\n\n")
    .replace(/^阶段\s*\d+\s*[：:]\s*/gm, "")
    .replace(/\n{3,}/g, "\n\n");
}

export function cleanAssistantMessageForDisplay(content = "") {
  const withoutProcessLines = String(content || "")
    .split(/\r?\n/)
    .filter((line) => !/^\s*(#{1,6}\s*)?阶段\s*\d+\s*[：:]/.test(line))
    .join("\n");
  const normalized = stripPhaseMarkers(withoutProcessLines);
  return normalized
    .split(/\r?\n/)
    .filter((line) => !MESSAGE_NOISE_PATTERNS.some((pattern) => pattern.test(line)))
    .join("\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
