"""Compatibility entry points: age is never evidence that work is disposable."""
from __future__ import annotations

import asyncio
import re

from nanocursor.worktree.manager import WorktreeManager

EPHEMERAL_PATTERNS = [
    re.compile(r"^agent-a[0-9a-f]{7}$"),
    re.compile(r"^wf_[0-9a-f]{8}-[0-9a-f]{3}-\d+$"),
    re.compile(r"^wf-\d+$"),
    re.compile(r"^bridge-[A-Za-z0-9_]+(-[A-Za-z0-9_]+)*$"),
    re.compile(r"^job-[a-zA-Z0-9._-]{1,55}-[0-9a-f]{8}$"),
]


def _is_ephemeral(name: str) -> bool:
    return any(pattern.match(name) for pattern in EPHEMERAL_PATTERNS)


async def cleanup_stale_worktrees(manager: WorktreeManager, cutoff_hours: int) -> int:
    """Keep all checkouts, including ignored artifacts and apparently clean trees."""
    return 0


async def start_stale_cleanup_task(manager: WorktreeManager, interval: int, cutoff_hours: int) -> None:
    # Existing callers may retain the task handle; only cancellation ends it.
    await asyncio.Future()
