"""
错误恢复模块 (Error Recovery)
在达到最大重试次数熔断前，保存工作区文件状态快照和修改日志，
确保用户仍能看到"尽力而为"的成果。
"""

import json
import os
import shutil
from datetime import datetime

from src.core.config import WORKSPACE_DIR
from src.core.logger import logger

SNAPSHOTS_DIR = os.path.join(WORKSPACE_DIR, ".snapshots")
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)


def create_workspace_snapshot(
    state: dict,
    reason: str = "max_retries_reached",
) -> str | None:
    """
    在达到最大重试次数前，将当前工作区的代码和修改日志打包为快照。

    包含内容：
    - 所有 .py 文件的内容（.backups 和 .snapshots 除外）
    - active_files 的完整备份（从 .backups 复制）
    - modification_log（如果 state 中有）
    - error_trace（最后的错误信息）
    - snapshot 元信息（时间、原因、active_files）

    Returns:
        快照目录路径，如果失败则返回 None
    """
    try:
        snapshot_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_dir = os.path.join(SNAPSHOTS_DIR, f"snapshot_{snapshot_id}")
        os.makedirs(snapshot_dir, exist_ok=True)

        # 1. 复制所有 .py 文件（排除 .backups, .snapshots）
        code_dir = os.path.join(snapshot_dir, "code")
        os.makedirs(code_dir, exist_ok=True)
        _copy_python_files(WORKSPACE_DIR, code_dir)

        # 2. 保存 active_files 的完整内容
        active_files = state.get("active_files", [])
        active_info = {}
        for rel_path in active_files:
            abs_path = os.path.join(WORKSPACE_DIR, rel_path)
            if os.path.exists(abs_path):
                with open(abs_path, encoding="utf-8") as f:
                    active_info[rel_path] = f.read()
            else:
                active_info[rel_path] = None  # 文件不存在

        # 3. 保存修改日志
        modification_log = state.get("modification_log", [])

        # 4. 保存当前会话消息中的关键信息
        messages = state.get("messages", [])
        conversation_summary = _extract_conversation_summary(messages)

        # 写元数据文件
        metadata = {
            "snapshot_id": snapshot_id,
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "active_files": active_files,
            "error_trace": state.get("error_trace", ""),
            "retry_count": state.get("retry_count", 0),
            "max_retries": state.get("max_retries", 0),
        }
        metadata_path = os.path.join(snapshot_dir, "metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        # 写 active_files 内容
        active_path = os.path.join(snapshot_dir, "active_files.json")
        with open(active_path, "w", encoding="utf-8") as f:
            json.dump(active_info, f, ensure_ascii=False, indent=2)

        # 写修改日志
        if modification_log:
            log_path = os.path.join(snapshot_dir, "modification_log.json")
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(modification_log, f, ensure_ascii=False, indent=2)

        # 写对话摘要
        summary_path = os.path.join(snapshot_dir, "conversation_summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(conversation_summary, f, ensure_ascii=False, indent=2)

        logger.info(
            f"工作区快照已创建: {snapshot_dir} "
            f"(原因: {reason}, 重试: {state.get('retry_count', 0)}/{state.get('max_retries', 0)})"
        )
        return snapshot_dir

    except Exception as e:
        logger.error(f"创建工作区快照失败: {e}")
        return None


def get_latest_snapshots_dir() -> str | None:
    """获取最新的快照目录。"""
    if not os.path.exists(SNAPSHOTS_DIR):
        return None
    try:
        snapshots = sorted(os.listdir(SNAPSHOTS_DIR))
        if not snapshots:
            return None
        return os.path.join(SNAPSHOTS_DIR, snapshots[-1])
    except Exception:
        return None


# ---------------------------------------------------------------
# 内部函数
# ---------------------------------------------------------------

def _copy_python_files(src_dir: str, dest_dir: str):
    """递归复制所有 .py 文件到目标目录。"""
    for item in os.listdir(src_dir):
        src_path = os.path.join(src_dir, item)
        if item.startswith((".backups", ".snapshots")):
            continue
        if os.path.isdir(src_path):
            sub_dest = os.path.join(dest_dir, item)
            os.makedirs(sub_dest, exist_ok=True)
            _copy_python_files(src_path, sub_dest)
        elif item.endswith(".py"):
            dest_path = os.path.join(dest_dir, item)
            shutil.copy2(src_path, dest_path)


def _extract_conversation_summary(messages) -> dict:
    """从消息中提取简化的对话摘要。"""
    user_requests = []
    assistant_responses = []
    tool_results = []
    for msg in messages:
        msg_type = getattr(msg, 'type', '') or getattr(msg, 'name', '')
        content = getattr(msg, 'content', str(msg))[:500]
        if msg_type in ('human', 'user'):
            user_requests.append(content)
        elif msg_type in ('ai', 'assistant') or getattr(msg, 'name', None) in (
            'Planner', 'Coder', 'Reviewer'
        ):
            assistant_responses.append({"role": msg_type or getattr(msg, 'name', ''), "content": content})
        elif msg_type == 'tool' or hasattr(msg, 'tool_call_id'):
            tool_results.append(f"{getattr(msg, 'name', 'unknown')}: {content[:200]}")
    return {
        "user_requests": user_requests[-5:],
        "assistant_responses": assistant_responses[-10:],
        "tool_results": tool_results[-15:],
    }
