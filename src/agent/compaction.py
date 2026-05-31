"""Context compaction for agent conversations.

Reduces message size when context grows too large by truncating old tool results.
"""

from __future__ import annotations

import json


COMPACT_TOKEN_THRESHOLD = 50000
MAX_RECENT_TOOL_RESULTS = 3


def _content_to_dict(content) -> dict | list:
    """Convert Anthropic ContentBlock lists to JSON-serializable dicts."""
    if isinstance(content, list):
        return [_content_to_dict(block) for block in content]
    if hasattr(content, "type"):
        if content.type == "text":
            return {"type": "text", "text": content.text}
        if content.type == "thinking":
            return {"type": "thinking", "thinking": content.thinking, "signature": getattr(content, "signature", "")}
        if content.type == "tool_use":
            return {"type": "tool_use", "id": content.id, "name": content.name, "input": content.input}
        if content.type == "tool_result":
            return {"type": "tool_result", "tool_use_id": content.tool_use_id, "content": content.content}
        return {"type": content.type}
    return content


def micro_compact(messages: list) -> list:
    """Keep only the most recent N tool results, summarizing older ones."""
    result = []
    tool_result_count = 0
    for msg in messages:
        msg_dict = msg if isinstance(msg, dict) else {"role": msg.role, "content": msg.content}
        if msg_dict.get("role") == "user" and isinstance(msg_dict.get("content"), list):
            new_content = []
            for block in msg_dict["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if tool_result_count < MAX_RECENT_TOOL_RESULTS:
                        new_content.append(block)
                        tool_result_count += 1
                    else:
                        new_content.append({
                            "type": "tool_result",
                            "tool_use_id": block.get("tool_use_id", "unknown"),
                            "content": f"[{len(block.get('content', ''))} chars tool output]",
                        })
                else:
                    new_content.append(block)
            msg_dict["content"] = new_content
        result.append(msg_dict)
    return result


def auto_compact(messages: list) -> list:
    """Auto-compact messages if serialized size exceeds threshold."""
    serializable = []
    for msg in messages:
        if isinstance(msg, dict) and "content" in msg:
            msg = dict(msg)
            msg["content"] = _content_to_dict(msg["content"])
        serializable.append(msg)
    size = len(json.dumps(serializable))
    if size > COMPACT_TOKEN_THRESHOLD:
        return micro_compact(messages)
    return messages
