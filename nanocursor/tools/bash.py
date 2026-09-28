from __future__ import annotations

import asyncio
import os
import re
import shlex
import signal
import shutil
import time

from nanocursor.recovery import RecoveryError, current_runtime as recovery_runtime
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from nanocursor.tools.base import Tool, ToolResult
from nanocursor.tools.runtime import current_runtime

if TYPE_CHECKING:
    from nanocursor.sandbox import Sandbox, SandboxConfig

MAX_TIMEOUT = 600
MAX_CAPTURE_BYTES = 1024 * 1024


async def _capture_output(proc: asyncio.subprocess.Process) -> str:
    """Drain the pipe to EOF while retaining only a bounded head and tail."""
    head = bytearray()
    tail = bytearray()
    total = 0
    head_limit = MAX_CAPTURE_BYTES // 2
    tail_limit = MAX_CAPTURE_BYTES - head_limit
    assert proc.stdout is not None
    while chunk := await proc.stdout.read(64 * 1024):
        total += len(chunk)
        take = min(len(chunk), head_limit - len(head))
        head.extend(chunk[:take])
        tail.extend(chunk[take:])
        if len(tail) > tail_limit:
            del tail[:len(tail) - tail_limit]
    await proc.wait()
    if total <= MAX_CAPTURE_BYTES:
        return (head + tail).decode(errors="replace")
    dropped = total - len(head) - len(tail)
    return (f"[Output truncated: omitted {dropped} bytes; retained first and last output.]\n\n"
            + head.decode(errors="replace")
            + "\n\n[... middle output omitted ...]\n\n"
            + tail.decode(errors="replace"))

# 特殊命令的退出码语义映射
# 这些命令的 exit code 1 不代表错误，只有 >= 阈值才算真正的错误
# 例如 grep 返回 1 仅表示"没有匹配行"，不是执行出错
_COMMAND_ERROR_THRESHOLDS: dict[str, int] = {
    "grep": 2,   # exit 1 = 没有匹配到内容
    "egrep": 2,
    "fgrep": 2,
    "rg": 2,     # ripgrep，与 grep 语义一致
    "diff": 2,   # exit 1 = 文件内容有差异
    "find": 2,   # exit 1 = 部分成功（如权限不足跳过某些目录）
    "test": 2,   # exit 1 = 条件为假
    "[": 2,      # test 的别名形式
}


def _extract_last_command_name(command: str) -> str | None:
    """从命令字符串中提取最后一个管道段的基础命令名。

    管道中最后一个命令决定了整体退出码，所以只看最后一段。
    例如 "cat file | grep pattern" → "grep"
    """
    # 按管道符拆分，取最后一段
    last_segment = command.rsplit("|", maxsplit=1)[-1].strip()
    if not last_segment:
        return None

    # 跳过常见的环境变量赋值前缀，如 "FOO=bar command ..."
    # 也要处理 sudo/env 等包装命令
    try:
        tokens = shlex.split(last_segment)
    except ValueError:
        # shlex 解析失败时，用简单的空格分割兜底
        tokens = last_segment.split()

    for token in tokens:
        # 跳过形如 VAR=VALUE 的环境变量赋值
        if re.match(r"^[A-Za-z_]\w*=", token):
            continue
        # 取 basename（去掉路径前缀，如 /usr/bin/grep → grep）
        base = token.rsplit("/", maxsplit=1)[-1]
        return base

    return None


def _interpret_exit_code(command: str, exit_code: int) -> bool:
    """根据命令语义判断退出码是否代表真正的错误。

    返回 True 表示是错误，False 表示不是错误。
    """
    if exit_code == 0:
        return False

    cmd_name = _extract_last_command_name(command)
    if cmd_name and cmd_name in _COMMAND_ERROR_THRESHOLDS:
        # 只有退出码 >= 阈值时才视为错误
        return exit_code >= _COMMAND_ERROR_THRESHOLDS[cmd_name]

    # 默认行为：非零退出码即为错误
    return True


# 特殊命令的退出码提示信息
# 帮助 LLM 理解非零退出码的含义，而不是简单地标记为错误
_EXIT_CODE_HINTS: dict[str, str] = {
    "grep": "no matches found",
    "egrep": "no matches found",
    "fgrep": "no matches found",
    "rg": "no matches found",
    "diff": "files differ",
    "find": "some directories were inaccessible",
    "test": "condition is false",
    "[": "condition is false",
}


def _exit_code_hint(command: str, exit_code: int) -> str:
    """为非零退出码生成可读提示。

    对于特殊命令（grep/diff/test 等），附加语义说明让 LLM 理解退出码含义。
    普通命令只显示退出码数字。
    """
    cmd_name = _extract_last_command_name(command)
    hint = _EXIT_CODE_HINTS.get(cmd_name, "") if cmd_name else ""
    if hint:
        return f"Exit code {exit_code} ({hint})"
    return f"Exit code {exit_code}"


class Params(BaseModel):
    command: str = Field(description="Shell command to execute")
    timeout: int = Field(default=120, ge=1, le=MAX_TIMEOUT, description="Timeout in seconds (max 600)")


class Bash(Tool):
    name = "Bash"
    description = "Execute a shell command and return stdout and stderr."
    params_model = Params
    category = "command"

    # 工作目录，为 None 时使用当前进程的工作目录
    work_dir: str | None = None

    # OS 级沙箱实例和配置（由外部注入，为 None 时不启用沙箱）
    sandbox: Sandbox | None = None
    sandbox_config: SandboxConfig | None = None

    def effective_sandbox_config(self, sandbox_root: Path | None = None) -> SandboxConfig | None:
        config = self.sandbox_config
        if config and sandbox_root:
            config = replace(config, allow_write=[str(sandbox_root), "/tmp"],
                             deny_write=[*config.deny_write,
                                         str(sandbox_root / ".nanocursor/config.yaml"),
                                         str(sandbox_root / ".nanocursor/permissions.local.yaml")])
        return config

    def execution_details(self, cwd: str, sandbox_root: str | None = None) -> dict:
        """Describe the same conditions used by execute, not the auto-allow flag."""
        config = self.effective_sandbox_config(Path(sandbox_root) if sandbox_root else None)
        active = bool(self.sandbox and config and self.sandbox.available())
        return {
            "cwd": str(Path(cwd).resolve()),
            "shell": (shutil.which("bash") or "bash (PATH)") if active else
                     ("/bin/sh" if os.name == "posix" else "platform default shell"),
            "sandbox_active": active,
            "sandbox_backend": type(self.sandbox).__name__ if active else None,
            "write_scope": [str(Path(p).resolve()) for p in config.allow_write]
                           if active else "process_permissions",
            "deny_write": [str(Path(p).resolve()) for p in config.deny_write] if active else [],
            "read_scope": "process_permissions (no read isolation)",
            "network": ("allowed" if config.network_enabled else "blocked") if active else
                       "process_permissions (not isolated)",
            "inherits_environment": True,
        }

    async def execute(self, params: Params) -> ToolResult:
        timeout = min(params.timeout, MAX_TIMEOUT)

        # 如果启用了 OS 沙箱，将命令包装为沙箱内执行
        actual_command = params.command
        context = current_runtime()
        config = self.effective_sandbox_config(context.sandbox_root if context else None)
        if self.sandbox and self.sandbox_config and self.sandbox.available():
            actual_command = self.sandbox.wrap(params.command, config)

        proc = None
        try:
            proc = await _spawn_owned_shell(
                actual_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # 合并 stderr 到 stdout
                cwd=str(context.cwd) if context else self.work_dir,
                start_new_session=os.name == "posix",
            )
            output = await asyncio.wait_for(_capture_output(proc), timeout=timeout)
        except asyncio.TimeoutError:
            if proc is not None:
                await _terminate_process_group(proc)
            return ToolResult(output=f"Error: command timed out after {timeout}s", is_error=True)
        except asyncio.CancelledError:
            if proc is not None:
                await _terminate_process_group(proc)
            raise
        except RecoveryError:
            if proc is not None:
                await _terminate_process_group(proc)
            raise
        except Exception as e:
            if proc is not None:
                await _terminate_process_group(proc)
            return ToolResult(output=f"Error executing command: {e}", is_error=True,
                              outcome_unknown=proc is not None)

        # 非零退出码时追加退出码信息，但 is_error 始终为 False
        # 只有超时和异常才设置 is_error=True
        _record_process(proc)
        exit_code = proc.returncode or 0
        if exit_code != 0:
            hint = _exit_code_hint(params.command, exit_code)
            if output:
                output = f"{output.rstrip()}\n\n{hint}"
            else:
                output = hint

        if not output:
            output = "(no output)"

        return ToolResult(output=output, is_error=False)


async def _spawn_owned_shell(command: str, **kwargs: object) -> asyncio.subprocess.Process:
    creation = asyncio.create_task(asyncio.create_subprocess_shell(command, **kwargs))
    try:
        proc = await asyncio.shield(creation)
        try:
            _record_process(proc)
        except RecoveryError:
            await _terminate_process_group(proc)
            raise
        return proc
    except asyncio.CancelledError:
        # Cancellation can arrive after fork but before the Process is returned.
        # Finish acquiring ownership before terminating the new process group.
        try:
            proc = await creation
        except Exception:
            raise asyncio.CancelledError from None
        await _terminate_process_group(proc)
        raise


async def _terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    async def drain(stream) -> None:
        if stream is not None:
            while await stream.read(64 * 1024):
                pass

    # A full pipe can prevent the subprocess transport from completing wait(),
    # even after the process has exited. Discard pending output during cleanup.
    drains = [asyncio.create_task(drain(s)) for s in (proc.stdout, proc.stderr) if s is not None]
    def stop(sig: int) -> None:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, sig)
            elif proc.returncode is None:
                proc.kill()
        except ProcessLookupError:
            pass
    stop(signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=1)
    except asyncio.TimeoutError:
        pass
    finally:
        # Descendants may outlive the shell or ignore SIGTERM.
        stop(signal.SIGKILL)
        await proc.wait()
        if drains:
            await asyncio.gather(*drains, return_exceptions=True)
        runtime = recovery_runtime()
        if not runtime or not runtime.store.failed:
            _record_process(proc)


def _record_process(proc: asyncio.subprocess.Process) -> None:
    runtime = recovery_runtime()
    if runtime and runtime.current_operation_id:
        previous = runtime.store.get_metadata("process", runtime.current_operation_id, {})
        runtime.store.put_metadata("process", runtime.current_operation_id, {
            "pid": proc.pid, "pgid": proc.pid if os.name == "posix" else None,
            "started_at": previous.get("started_at", time.time()),
            "observed_at": time.time(), "exit_code": proc.returncode,
            "owner_token": runtime.owner_token,
            "notice": "Process identity is a historical clue, not permission to kill this PID after restart.",
        })
