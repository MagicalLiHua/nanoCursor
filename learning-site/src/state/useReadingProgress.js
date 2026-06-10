import { useCallback, useMemo, useState } from "react";

const STORAGE_KEY = "nanocursor.learning.readProgress";

function loadProgress() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveProgress(value) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
}

export function useReadingProgress(documents) {
  const [progress, setProgress] = useState(loadProgress);

  const markVisited = useCallback((id) => {
    if (!id) return;
    setProgress((current) => {
      const next = {
        ...current,
        [id]: {
          ...(current[id] || {}),
          lastReadAt: new Date().toISOString(),
        },
      };
      saveProgress(next);
      return next;
    });
  }, []);

  const toggleCompleted = useCallback((id) => {
    setProgress((current) => {
      const prev = current[id] || {};
      const next = {
        ...current,
        [id]: {
          ...prev,
          completed: !prev.completed,
          lastReadAt: new Date().toISOString(),
        },
      };
      saveProgress(next);
      return next;
    });
  }, []);

  const stats = useMemo(() => {
    const chapterIds = documents.filter((doc) => doc.group === "chapters").map((doc) => doc.id);
    const completed = chapterIds.filter((id) => progress[id]?.completed).length;
    const recentId = Object.entries(progress)
      .sort((a, b) => String(b[1]?.lastReadAt || "").localeCompare(String(a[1]?.lastReadAt || "")))[0]?.[0];
    return {
      total: chapterIds.length,
      completed,
      percent: chapterIds.length ? Math.round((completed / chapterIds.length) * 100) : 0,
      recentId,
    };
  }, [documents, progress]);

  return { progress, stats, markVisited, toggleCompleted };
}
