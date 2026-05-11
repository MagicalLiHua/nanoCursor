"""
Error Recovery - 借鉴 s11_error_recovery.py

三种错误恢复策略：
1. max_tokens 溢出 → 注入续写消息重试
2. prompt_too_long → 自动压缩后重试
3. 连接/限流错误 → 指数退避重试
"""

import random
import time
from typing import Callable, Any


# 配置常量
MAX_RECOVERY_ATTEMPTS = 3
BACKOFF_BASE_DELAY = 1.0
BACKOFF_MAX_DELAY = 30.0
TOKEN_THRESHOLD = 50000

# 续写消息
CONTINUATION_MESSAGE = """Output limit reached. Please continue directly from where you stopped, without repeating previous content."""


def backoff_delay(attempt: int) -> float:
    """
    计算指数退避延迟。
    delay = base * 2^attempt + random(0, 1)
    """
    delay = BACKOFF_BASE_DELAY * (2 ** attempt) + random.random()
    return min(delay, BACKOFF_MAX_DELAY)


def estimate_tokens(messages: list) -> int:
    """估算消息 token 数（粗略）"""
    import json
    return len(json.dumps(messages)) // 4


def wrap_with_retry(
    call_fn: Callable,
    *args,
    **kwargs
) -> tuple[Any, str | None]:
    """
    包装 LLM 调用，支持错误恢复。

    返回 (result, error_message)。如果成功，error 为 None。
    """
    attempt = 0
    last_error = None

    while attempt < MAX_RECOVERY_ATTEMPTS:
        try:
            result = call_fn(*args, **kwargs)
            return result, None

        except Exception as e:
            error_str = str(e)
            last_error = error_str

            # 判断错误类型
            if "max_tokens" in error_str.lower() or "token limit" in error_str.lower():
                # 注入续写消息，让 LLM 继续生成
                if args and len(args) > 1:
                    messages = list(args[1])
                    messages.append({"role": "user", "content": CONTINUATION_MESSAGE})
                    args = (args[0], messages) + args[2:]
                attempt += 1
                continue

            elif "prompt_too_long" in error_str.lower() or "context_length" in error_str.lower():
                # 需要压缩上下文
                return None, "prompt_too_long"

            else:
                # 连接错误或限流，使用退避重试
                if attempt < MAX_RECOVERY_ATTEMPTS - 1:
                    delay = backoff_delay(attempt)
                    time.sleep(delay)
                    attempt += 1
                else:
                    break

    return None, last_error


async def async_wrap_with_retry(
    call_fn: Callable,
    *args,
    **kwargs
) -> tuple[Any, str | None]:
    """异步版本的错误包装"""
    import asyncio
    attempt = 0
    last_error = None

    while attempt < MAX_RECOVERY_ATTEMPTS:
        try:
            if asyncio.iscoroutinefunction(call_fn):
                result = await call_fn(*args, **kwargs)
            else:
                result = call_fn(*args, **kwargs)
            return result, None

        except Exception as e:
            error_str = str(e)
            last_error = error_str

            if "max_tokens" in error_str.lower() or "token limit" in error_str.lower():
                if args and len(args) > 1:
                    messages = list(args[1])
                    messages.append({"role": "user", "content": CONTINUATION_MESSAGE})
                    args = (args[0], messages) + args[2:]
                attempt += 1
                continue

            elif "prompt_too_long" in error_str.lower() or "context_length" in error_str.lower():
                return None, "prompt_too_long"

            else:
                if attempt < MAX_RECOVERY_ATTEMPTS - 1:
                    delay = backoff_delay(attempt)
                    await asyncio.sleep(delay)
                    attempt += 1
                else:
                    break

    return None, last_error


__all__ = [
    "MAX_RECOVERY_ATTEMPTS", "BACKOFF_BASE_DELAY", "BACKOFF_MAX_DELAY",
    "TOKEN_THRESHOLD", "CONTINUATION_MESSAGE",
    "backoff_delay", "estimate_tokens", "wrap_with_retry", "async_wrap_with_retry"
]