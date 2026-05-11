"""
轻量级消息类型，替代 langchain_core.messages。
基于 dataclass 实现，兼容 Pydantic 验证。
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


@dataclass
class BaseMessage:
    """所有消息类型的基类"""
    role: str = "user"
    content: str | list[dict] = ""
    name: Optional[str] = None

    def to_dict(self) -> dict:
        """序列化为字典（用于 LLM API 调用）"""
        result = {"role": self.role, "content": self.content}
        if self.name:
            result["name"] = self.name
        return result


@dataclass
class HumanMessage(BaseMessage):
    """用户消息"""
    role: Literal["user"] = "user"


@dataclass
class AIMessage(BaseMessage):
    """AI 助手消息"""
    role: Literal["assistant"] = "assistant"
    content: str | list[dict] = ""
    tool_calls: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        result = super().to_dict()
        if self.tool_calls:
            # 如果 content 不是 blocks 列表，将 tool_calls 转换为 tool_use blocks
            # 如果 content 已经是 blocks 列表（来自 resp.raw.content），不需要重复添加 tool_use
            if not isinstance(self.content, list):
                content_str = str(self.content) if self.content else ""
                result["content"] = [{"type": "text", "text": content_str}] if content_str else []
                for tc in self.tool_calls:
                    tc_id = tc.get("id", "")
                    tc_name = tc.get("name", "")
                    tc_args = tc.get("args", {})
                    result["content"].append({
                        "type": "tool_use", "id": tc_id, "name": tc_name, "input": tc_args
                    })
            # 如果 content 已经是列表（包含 tool_use blocks），直接使用，不重复添加
            # 移除 tool_calls 字段（已经内联到 content）
            result.pop("tool_calls", None)
        return result


@dataclass
class SystemMessage(BaseMessage):
    """系统消息"""
    role: Literal["system"] = "system"

    def to_dict(self) -> dict:
        result = super().to_dict()
        result["type"] = "text"  # DeepSeek requires type field
        return result


@dataclass
class ToolMessage:
    """工具执行结果消息"""
    role: Literal["tool"] = "tool"
    name: str = ""
    content: str = ""
    tool_call_id: str = ""

    def to_dict(self) -> dict:
        return {
            "role": "tool",
            "name": self.name,
            "content": self.content,
            "tool_call_id": self.tool_call_id,
        }


# ==========================================
# 辅助函数
# ==========================================

def messages_to_api_format(messages: list) -> list[dict]:
    """
    将消息列表转换为 LLM API 格式（list of dicts）。
    处理 LangChain 消息、我们的 dataclass 消息、或 dict 的混合情况。
    """
    result = []
    for msg in messages:
        if isinstance(msg, dict):
            if msg.get("role") == "system" and "type" not in msg:
                msg = dict(msg)
                msg["type"] = "text"
            result.append(msg)
        elif hasattr(msg, 'to_dict'):
            d = msg.to_dict()
            if d.get("role") == "system" and "type" not in d:
                d["type"] = "text"
            result.append(d)
        elif hasattr(msg, 'content'):
            # 兼容 LangChain 消息类型
            d = {"role": getattr(msg, 'type', 'user'), "content": msg.content}
            if d["role"] == "system":
                d["type"] = "text"
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                d["tool_calls"] = msg.tool_calls
            result.append(d)
        else:
            result.append({"role": "user", "content": str(msg)})
    return result


def from_langchain_message(msg) -> BaseMessage | ToolMessage:
    """
    从 LangChain 消息类型转换为我们的轻量级类型。
    用于渐进式迁移过程中的兼容层。
    """
    if hasattr(msg, 'tool_call_id'):
        return ToolMessage(
            name=getattr(msg, 'name', 'unknown'),
            content=msg.content,
            tool_call_id=msg.tool_call_id,
        )
    elif msg.__class__.__name__.endswith('AIMessage'):
        return AIMessage(
            role="assistant",
            content=msg.content,
            name=getattr(msg, 'name', None),
            tool_calls=getattr(msg, 'tool_calls', []),
        )
    elif msg.__class__.__name__.endswith('HumanMessage'):
        return HumanMessage(role="user", content=msg.content)
    elif msg.__class__.__name__.endswith('SystemMessage'):
        return SystemMessage(role="system", content=msg.content)
    else:
        return HumanMessage(role="user", content=str(msg))
