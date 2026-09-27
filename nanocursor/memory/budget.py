"""Deterministic, conservative estimates for bounded memory text (not billing)."""
from __future__ import annotations

import math


def estimate(text: str) -> int:
    # Non-ASCII may take multiple tokens. This intentionally budgets it more
    # conservatively than the conversation's rough ASCII character heuristic.
    return math.ceil(sum(1 if ord(c) < 128 else 8 for c in text) / 4)


def clip(text: str, tokens: int, *, marker: str = "\n[truncated]") -> str:
    """Fit both estimated tokens and UTF-8 bytes, including the suffix."""
    tokens = max(0, tokens)
    if estimate(text) <= tokens and len(text.encode("utf-8")) <= tokens * 4:
        return text
    if estimate(marker) > tokens:
        return ""
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        candidate = text[:mid] + marker
        if estimate(candidate) <= tokens and len(candidate.encode("utf-8")) <= tokens * 4:
            low = mid
        else:
            high = mid - 1
    return text[:low] + marker if low else ""


def limit(window: int, configured: int, *, headroom: int | None = None) -> int:
    cap = max(0, min(configured, window // 20))
    return cap if headroom is None else max(0, min(cap, headroom))
