"""
Bash 命令执行工具模块
在 Docker 隔离环境中执行 shell 命令，确保安全隔离。
"""

import docker
import subprocess
import os

from src.infra.config import (
    SANDBOX_CONTAINER_STARTUP_TIMEOUT,
    SANDBOX_IMAGE,
    SANDBOX_MEM_LIMIT,
    WORKSPACE_DIR,
)
from src.infra.logger import logger
from src.infra.tools import ToolRegistry

try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"[系统警告] 无法连接到 Docker 守护进程: {e}")
    docker_client = None


def _sanitize_shell_arg(s: str) -> str:
    """Escape single quotes in a string so it is safe inside bash -c '...'."""
    return s.replace("'", "'\\''")


async def run_bash(workspace: str, command: str) -> str:
    """
    在 Docker 隔离环境中执行 bash 命令。

    Args:
        workspace: 工作区根目录（用于 volume 挂载）
        command: 要执行的 shell 命令

    Returns:
        命令执行结果或错误信息
    """
    if not docker_client:
        return "错误：Docker 客户端未启动，无法执行命令。"

    if not command or not command.strip():
        return "错误：命令不能为空。"

    # 安全检查：禁止危险命令
    dangerous_patterns = ["--privileged", "mount --bind", "chroot", "rm -rf /", "mkfs", ":(){ :|:& };:"]
    for pattern in dangerous_patterns:
        if pattern in command:
            return f"错误：禁止执行危险命令模式: {pattern}"

    logger.info(f"[Bash] 执行命令: {command}")

    try:
        safe_cmd = _sanitize_shell_arg(command)
        full_command = f"sh -c '{safe_cmd}'"

        container = docker_client.containers.create(
            image=SANDBOX_IMAGE,
            command=full_command,
            volumes={
                WORKSPACE_DIR: {'bind': '/workspace', 'mode': 'rw'}
            },
            working_dir="/workspace",
            mem_limit=SANDBOX_MEM_LIMIT,
            network_disabled=True,
            auto_remove=True,
        )

        container.start()
        result = container.wait(timeout=60)
        exit_code = result.get("StatusCode", 0)
        logs = container.logs(stdout=True, stderr=True).decode("utf-8")

        if exit_code == 0:
            return logs if logs else "命令执行成功（无输出）"
        else:
            return f"[Exit Code {exit_code}]\n{logs}"

    except docker.errors.ImageNotFound:
        return "错误：Docker 镜像不存在，请等待系统初始化后重试。"

    except Exception as e:
        logger.error(f"Bash 执行失败: {e}")
        return f"命令执行失败: {e}"


# ==========================================
# 工具 Schema（OpenAI 格式）
# ==========================================

BASH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_bash",
        "description": "在 Docker 隔离环境中执行 bash 命令。适用于运行构建脚本、安装依赖、运行测试等场景。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令。例如: 'pip install -r requirements.txt', 'pytest tests/', 'ls -la'",
                },
            },
            "required": ["command"],
        },
    },
}


def register_bash_tools():
    """注册 bash 工具到 ToolRegistry"""
    ToolRegistry.register("run_bash", run_bash, BASH_TOOL_SCHEMA)


def register_tools():
    """供外部调用的统一注册入口"""
    register_bash_tools()