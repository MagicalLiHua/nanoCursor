"""Process-local admission guard for starting workflow runs."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


_start_times: dict[str, list[float]] = {}
_lock = threading.RLock()


def check_run_start_rate_limit(
    thread_id: str,
    *,
    active_runs: dict,
    runs_lock,
    min_interval_seconds: int = 10,
    clock: Callable[[], float] = time.time,
) -> tuple[bool, str]:
    """Reject duplicate active runs and rapid restarts for one thread id."""
    now = clock()
    with runs_lock:
        run_info = active_runs.get(thread_id)
    if run_info and run_info.get("status") not in {"completed", "failed", "cancelled", "interrupted"}:
        return False, f"线程 {thread_id} 已有一个工作流在运行中，请等待完成后再试"

    with _lock:
        recent = [started_at for started_at in _start_times.get(thread_id, []) if now - started_at < min_interval_seconds]
        if recent:
            wait_time = max(0, int(min_interval_seconds - (now - max(recent))))
            return False, f"工作流启动过于频繁，请等待 {wait_time} 秒后再试"

        recent.append(now)
        _start_times[thread_id] = recent[-10:]
    return True, ""


def clear_run_start_rate_limit(thread_id: str | None = None) -> None:
    """Clear admission history for tests and administrative recovery."""
    with _lock:
        if thread_id is None:
            _start_times.clear()
        else:
            _start_times.pop(thread_id, None)


__all__ = ["check_run_start_rate_limit", "clear_run_start_rate_limit"]
