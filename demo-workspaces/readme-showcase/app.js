export function summarizeTasks(tasks) {
  const done = tasks.filter((task) => task.done).length;
  return {
    total: tasks.length,
    done,
    remaining: tasks.length - done
  };
}

export function formatSummary(summary) {
  return `${summary.done}/${summary.total} done`;
}

export function completionRate(summary) {
  if (summary.total === 0) return "0%";
  const percent = Math.round((summary.done / summary.total) * 100);
  return `${percent}%`;
}
