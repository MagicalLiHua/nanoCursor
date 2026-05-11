"""
Context Compactor - 借鉴 s06_context_compact.py

三层次上下文管理：
1. 大型工具输出持久化到磁盘
2. 微型压缩：超过3个的旧工具结果用占位符替换
3. 完整压缩：调用 LLM 总结历史会话
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from src.infra.config import WORKSPACE_DIR
from src.agent.engine import MODEL, create_client

WORKDIR = Path(WORKSPACE_DIR)


OUTPUT_DIR = WORKDIR / ".task_outputs"
TRANSCRIPTS_DIR = WORKDIR / ".transcripts"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

# 压缩阈值配置
LARGE_OUTPUT_THRESHOLD = 30000  # bytes
COMPACT_TOKEN_THRESHOLD = 50000  # tokens
MAX_RECENT_TOOL_RESULTS = 3  # 保留最近 N 个工具结果


@dataclass
class CompactState:
    """压缩状态跟踪"""
    session_id: str
    compactions: int = 0
    persisted_files: list[str] = field(default_factory=list)
    last_compact_at: float = 0.0


def persist_large_output(tool_use_id: str, output: str) -> str:
    """
    将大型工具输出持久化到磁盘，返回预览。
    """
    if len(output) < LARGE_OUTPUT_THRESHOLD:
        return output

    output_file = OUTPUT_DIR / f"{tool_use_id}.txt"
    output_file.write_text(output, encoding="utf-8")

    preview = output[:500] + f"\n... [Output persisted to {output_file.name}, total {len(output)} chars]"
    return preview


def micro_compact(messages: list) -> list:
    """
    微型压缩：保留最近 MAX_RECENT_TOOL_RESULTS 个工具结果，
    其余的替换为占位符。
    """
    result = []
    tool_result_count = 0

    for msg in messages:
        msg_dict = msg if isinstance(msg, dict) else {"role": msg.role, "content": msg.content}

        if msg_dict.get("role") == "user" and isinstance(msg_dict.get("content"), list):
            # 处理 content 中的 tool_result 块
            new_content = []
            for block in msg_dict["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if tool_result_count < MAX_RECENT_TOOL_RESULTS:
                        new_content.append(block)
                        tool_result_count += 1
                    else:
                        # 替换为占位符
                        tool_id = block.get("tool_use_id", "unknown")
                        new_content.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": f"[{len(block.get('content', ''))} chars tool output - see {OUTPUT_DIR.name}]"
                        })
                else:
                    new_content.append(block)
            msg_dict["content"] = new_content

        result.append(msg_dict)

    return result


def estimate_context_size(messages: list) -> int:
    """估算上下文大小（字符数）"""
    return len(json.dumps(messages))


def summarize_history(messages: list, client=None) -> str:
    """
    调用 LLM 总结历史会话，返回摘要。
    """
    if client is None:
        client = create_client()

    # 提取关键信息
    history_text = "\n".join([
        f"[{msg.get('role', 'user')}]: {msg.get('content', '')[:500]}"
        for msg in messages[-20:]  # 只取最近20条
    ])

    summary_prompt = f"""请总结以下对话的要点，保留关键决策和发现：

{history_text}

请用简洁的中文总结（不超过500字）：
"""

    try:
        resp = client.messages.create(
            model=MODEL,
            system="你是一个助手，负责总结对话要点。简洁回答。",
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=1000,
        )
        return "".join(b.text for b in resp.content if hasattr(b, "text"))
    except Exception as e:
        return f"[摘要生成失败: {e}]"


def compact_history(messages: list, state: CompactState = None) -> tuple[list, str]:
    """
    完整上下文压缩：
    1. 将会话记录写入 .transcripts/
    2. 调用 LLM 总结
    3. 用摘要替换历史消息
    """
    from dataclasses import dataclass, field

    session_id = str(int(time.time() * 1000)) if state is None else state.session_id
    transcript_file = TRANSCRIPTS_DIR / f"transcript_{session_id}.json"

    # 保存原始会话
    transcript_file.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")

    # 生成摘要
    summary = summarize_history(messages)

    # 替换为摘要消息
    compacted = [
        {"role": "system", "content": f"[会话摘要 - 原始记录见 {transcript_file.name}]"},
        {"role": "user", "content": f"【会话摘要】{summary}"},
    ]

    new_state = CompactState(
        session_id=session_id,
        compactions=(state.compactions + 1) if state else 1,
        persisted_files=[transcript_file.name],
        last_compact_at=time.time(),
    )

    return compacted, new_state


def auto_compact(messages: list) -> list:
    """
    自动压缩检查：如果超过阈值则压缩。
    """
    size = estimate_context_size(messages)
    if size > COMPACT_TOKEN_THRESHOLD:
        compacted, _ = compact_history(messages)
        return compacted
    return messages


__all__ = ["CompactState", "persist_large_output", "micro_compact", "compact_history", "summarize_history", "auto_compact", "estimate_context_size"]