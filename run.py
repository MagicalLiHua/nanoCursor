#!/usr/bin/env python3
"""
run.py - nanoCursor 入口（重构版）

参考 s_full.py / agents/ 目录重写。

不再用复杂的状态机和 schema，用简单的 while 循环 + 工具调用。
"""

import asyncio
import os
import uuid
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

from src.agent.engine import (
    agent_loop, run_subagent, TOOLS, TOOL_HANDLERS,
    WORKDIR, MODEL
)


def build_system_prompt() -> str:
    """简单的系统提示"""
    return f"""你是一个自动编程助手，在 {WORKDIR} 工作。

【重要】你运行在 Windows 系统上！使用 Windows 命令：
- dir 而不是 ls
- type 而不是 cat
- del 而不是 rm
- copy 而不是 cp

【可用工具】
- bash(command): 执行命令
- read_file(path, limit?): 读取文件
- write_file(path, content): 写文件
- edit_file(path, old_text, new_text): 编辑
- list_directory(path?): 列出目录

【任务】
探索项目，找 bug，修 bug，测试验证
"""


async def main():
    print(f"nanoCursor 重构版 ({MODEL})")
    print(f"工作目录: {WORKDIR}")
    print("-" * 40)

    user_prompt = """
我们的项目里有一个文件出了 Bug。我只记得它是一个查找算法相关的 Python 文件，但是我不记得它在哪个目录下了。
请帮我找出这个文件，读取它，并修复里面导致测试不通过的 Bug。
"""

    messages = [{"role": "user", "content": user_prompt}]
    system = build_system_prompt()

    # 添加工具
    all_tools = list(TOOLS)

    result = await agent_loop(messages, system, all_tools, max_turns=100)

    print("-" * 40)
    print(f"结果:\n{result[-800:]}")


if __name__ == "__main__":
    asyncio.run(main())