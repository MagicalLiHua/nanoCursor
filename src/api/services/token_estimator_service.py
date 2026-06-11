"""Conservative token estimation utilities.

This is deliberately tokenizer-free. It gives stable, explainable estimates for
budgeting and UI telemetry; provider-specific token counters can replace it
later without changing ledger consumers.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any


_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


CONTENT_CHAR_RATIOS = {
    "english": 4.0,
    "code": 3.0,
    "json": 3.0,
    "tool": 3.0,
    "mixed": 3.5,
    "chinese": 1.5,
}


def estimate_tokens(text: str | None, *, content_type: str = "mixed") -> int:
    if text is None:
        return 0
    value = str(text)
    if value == "":
        return 0

    cjk_chars = len(_CJK_RE.findall(value))
    non_cjk_chars = max(len(value) - cjk_chars, 0)
    ratio = CONTENT_CHAR_RATIOS.get(content_type, CONTENT_CHAR_RATIOS["mixed"])
    if content_type == "chinese":
        ratio = CONTENT_CHAR_RATIOS["chinese"]
    tokens = (cjk_chars / CONTENT_CHAR_RATIOS["chinese"]) + (non_cjk_chars / ratio)
    return max(1, int(math.ceil(tokens)))


def estimate_json_tokens(value: Any) -> int:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(value)
    return estimate_tokens(text, content_type="json")


def estimate_section_tokens(section: Any) -> int:
    if isinstance(section, str):
        return estimate_tokens(section)
    if isinstance(section, dict):
        content_type = str(section.get("content_type") or section.get("category") or "json")
        value = section.get("content", section.get("text", section))
        if isinstance(value, str):
            return estimate_tokens(value, content_type=content_type)
        return estimate_json_tokens(value)
    return estimate_json_tokens(section)
