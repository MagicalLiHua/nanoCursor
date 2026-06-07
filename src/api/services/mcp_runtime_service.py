"""MCP runtime service — probe, list tools, call tools.

The runtime uses the MCP stdio transport directly. It intentionally keeps each
operation short-lived: start server -> initialize -> list/call -> close. This is
slower than a pooled client, but much easier to reason about for local desktop
workflows and keeps broken MCP servers from poisoning later runs.
"""

from __future__ import annotations

import json
import hashlib
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.api.services.mcp_service import list_mcp_servers
from src.api.services.mcp_status_service import (
    get_mcp_server_status,
    record_mcp_usage,
    update_mcp_status,
)
from src.api.services.mcp_tool_catalog_service import build_mcp_tool_catalog
from src.runtime.go_runtime_client import GoRuntimeError, GoRuntimeUnavailable
from src.runtime.runtime_feature_flags import go_runtime_enabled
from src.runtime import go_mcp_gateway_client

try:
    from src.runtime import mcp_client as _mcp_grpc
    _MCP_GRPC_AVAILABLE = True
except ImportError:
    _MCP_GRPC_AVAILABLE = False


MCP_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT_SECONDS = 10
MAX_CAPTURE_CHARS = 20_000
TOOLS_CACHE_TTL_SECONDS = 300
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_OPEN_SECONDS = 60


def _mcp_server_config(server_id: str, workspace_dir: str | None = None) -> dict[str, Any] | None:
    """Find a single MCP server by id in the workspace config."""
    data = list_mcp_servers(workspace_dir)
    for s in data.get("servers", []):
        if s["id"] == server_id:
            return s
    return None


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _clip(value: str, max_chars: int = MAX_CAPTURE_CHARS) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n...[truncated]"


def _server_fingerprint(server: dict[str, Any]) -> str:
    payload = {
        "command": server.get("command", ""),
        "args": server.get("args", []),
        "env_keys": server.get("env_keys", []),
        "enabled": server.get("enabled", True),
        "source": server.get("source", ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _circuit_remaining_seconds(status: dict[str, Any]) -> int:
    open_until = float(status.get("circuit_open_until") or 0)
    remaining = int(open_until - time.time())
    return max(0, remaining)


def _cached_tools(status: dict[str, Any], fingerprint: str, *, allow_stale: bool = False) -> dict[str, Any] | None:
    cache = status.get("tools_cache")
    if not isinstance(cache, dict):
        return None
    if cache.get("config_hash") != fingerprint:
        return None
    cached_at = float(cache.get("cached_at") or 0)
    is_fresh = time.time() - cached_at <= TOOLS_CACHE_TTL_SECONDS
    if not is_fresh and not allow_stale:
        return None
    tools = cache.get("tools")
    if not isinstance(tools, list):
        return None
    return {
        "tools": tools,
        "cached_at": cached_at,
        "cache": "hit" if is_fresh else "stale",
    }


def _fallback_payload(
    *,
    used: bool,
    reason: str,
    strategy: str,
    can_continue: bool,
    recommended_action: str,
    source: str = "",
) -> dict[str, Any]:
    return {
        "used": used,
        "reason": reason,
        "strategy": strategy,
        "can_continue": can_continue,
        "recommended_action": recommended_action,
        "source": source,
    }


def _tool_discovery_fallback(
    server_id: str,
    server: dict[str, Any],
    status: dict[str, Any],
    fingerprint: str,
    error: str,
) -> dict[str, Any] | None:
    cached = _cached_tools(status, fingerprint, allow_stale=True)
    if not cached:
        return None
    return {
        "server_id": server_id,
        "command": server.get("command", ""),
        "tools": cached["tools"],
        "status": "degraded",
        "ok": False,
        "error": error,
        "transport": "stdio",
        "cache": "fallback_stale",
        "cached_at": cached["cached_at"],
        "fallback": _fallback_payload(
            used=True,
            reason=error,
            strategy="stale_tool_catalog",
            can_continue=True,
            recommended_action="继续使用上一次成功发现的 MCP 工具目录；真正调用前仍需重新检查 server 状态。",
            source="tools_cache",
        ),
    }


def _call_failure_fallback(
    server_id: str,
    tool_name: str,
    error: str,
) -> dict[str, Any]:
    lowered = f"{server_id}/{tool_name}".lower()
    read_like = any(token in lowered for token in ("read", "list", "search", "get", "fetch", "query"))
    if read_like:
        return _fallback_payload(
            used=False,
            reason=error,
            strategy="local_read_tools",
            can_continue=True,
            recommended_action="MCP 只读工具不可用；可退回项目索引、read_file、文件搜索等本地只读工具继续任务。",
            source="runtime_policy",
        )
    return _fallback_payload(
        used=False,
        reason=error,
        strategy="no_safe_automatic_fallback",
        can_continue=False,
        recommended_action="该 MCP 调用可能产生外部副作用，失败后不自动替代执行；需要用户确认或改用更安全的本地方案。",
        source="runtime_policy",
    )


def _record_mcp_success(
    server_id: str,
    workspace_dir: str,
    updates: dict[str, Any] | None = None,
) -> None:
    update_mcp_status(
        server_id,
        {
            "server_id": server_id,
            "status": "ready",
            "failure_count": 0,
            "last_error": "",
            "last_success_at": time.time(),
            "circuit_open_until": 0,
            **(updates or {}),
        },
        workspace_dir,
    )


def _record_mcp_failure(
    server_id: str,
    workspace_dir: str,
    error: str,
) -> dict[str, Any]:
    current = get_mcp_server_status(server_id, workspace_dir)
    failure_count = int(current.get("failure_count") or 0) + 1
    updates: dict[str, Any] = {
        "server_id": server_id,
        "status": "failed",
        "failure_count": failure_count,
        "last_error": error,
        "last_failed_at": time.time(),
    }
    if failure_count >= CIRCUIT_FAILURE_THRESHOLD:
        updates["status"] = "circuit_open"
        updates["circuit_open_until"] = time.time() + CIRCUIT_OPEN_SECONDS
    return update_mcp_status(server_id, updates, workspace_dir)


class _MCPStdioClient:
    """Tiny MCP stdio JSON-RPC client used by the backend service layer."""

    def __init__(self, server: dict[str, Any], workspace: Path, timeout_seconds: int) -> None:
        self.server = server
        self.workspace = workspace
        self.timeout_seconds = max(1, min(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), 60))
        self._next_id = 1
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_chunks: list[str] = []
        self._reader_error = ""
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def stderr(self) -> str:
        return _clip("".join(self._stderr_chunks))

    def __enter__(self) -> "_MCPStdioClient":
        command = str(self.server.get("command") or "").strip()
        if not command:
            raise ValueError("MCP server 未声明 command。")
        args = [str(item) for item in self.server.get("args", [])]
        env = dict(os.environ)
        self._process = subprocess.Popen(
            [command, *args],
            cwd=str(self.workspace),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        threading.Thread(target=self._read_stdout_loop, daemon=True).start()
        threading.Thread(target=self._read_stderr_loop, daemon=True).start()

        response = self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "nanoCursor", "version": "0.1.0"},
            },
        )
        if response.get("error"):
            raise RuntimeError(f"MCP initialize 失败: {response['error']}")
        self.notify("notifications/initialized", {})
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
        finally:
            self._process = None

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._write_message({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        })
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"MCP request timeout: {method}")
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty as exc:
                detail = f" stderr={self.stderr!r}" if self.stderr else ""
                raise TimeoutError(f"MCP request timeout: {method}.{detail}") from exc
            if message.get("id") == request_id:
                return message

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write_message({
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        })

    def _write_message(self, message: dict[str, Any]) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise RuntimeError("MCP process stdin is unavailable.")
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        process.stdin.write(header + body)
        process.stdin.flush()

    def _read_stdout_loop(self) -> None:
        process = self._require_process()
        if process.stdout is None:
            return
        try:
            while True:
                message = self._read_frame(process.stdout)
                if message is None:
                    return
                self._messages.put(message)
        except Exception as exc:
            self._reader_error = str(exc)

    def _read_stderr_loop(self) -> None:
        process = self._require_process()
        if process.stderr is None:
            return
        try:
            while True:
                chunk = process.stderr.readline()
                if not chunk:
                    return
                self._stderr_chunks.append(chunk.decode("utf-8", errors="replace"))
                if sum(len(item) for item in self._stderr_chunks) > MAX_CAPTURE_CHARS:
                    self._stderr_chunks = [self.stderr]
        except Exception:
            return

    def _read_frame(self, stream: Any) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        while True:
            line = stream.readline()
            if line == b"":
                return None
            if line in (b"\r\n", b"\n"):
                break
            key, _, value = line.decode("ascii", errors="replace").partition(":")
            headers[key.strip().lower()] = value.strip()

        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return None
        body = stream.read(length)
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def _require_process(self) -> subprocess.Popen[bytes]:
        if self._process is None:
            raise RuntimeError("MCP process is not running.")
        if self._process.poll() is not None:
            detail = f" stderr={self.stderr!r}" if self.stderr else ""
            raise RuntimeError(f"MCP process exited with code {self._process.returncode}.{detail}")
        if self._reader_error:
            raise RuntimeError(f"MCP stdout reader failed: {self._reader_error}")
        return self._process


def _run_mcp_request(
    server: dict[str, Any],
    workspace: Path,
    method: str,
    params: dict[str, Any],
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    with _MCPStdioClient(server, workspace, timeout_seconds) as client:
        response = client.request(method, params)
        response["stderr"] = client.stderr
        return response


def _normalise_mcp_error(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message") or error.get("error") or json.dumps(error, ensure_ascii=False)
        code = error.get("code")
        return f"{message} (code={code})" if code is not None else str(message)
    return str(error or "unknown MCP error")


def _go_mcp_probe(server_id: str, server: dict[str, Any], workspace: Path) -> dict[str, Any] | None:
    if _MCP_GRPC_AVAILABLE:
        try:
            result = _mcp_grpc.probe_server(
                server_id=server_id,
                command=str(server.get("command") or ""),
                args=[str(item) for item in server.get("args", [])],
                env_keys=[str(item) for item in server.get("env_keys", [])],
                workspace_dir=str(workspace),
            )
            if result.get("ok"):
                return result
        except Exception:
            pass
    if not go_runtime_enabled():
        return None
    try:
        return go_mcp_gateway_client.probe_mcp_server(
            server_id=server_id,
            workspace_dir=str(workspace),
            command=str(server.get("command") or ""),
            args=[str(item) for item in server.get("args", [])],
            env_keys=[str(item) for item in server.get("env_keys", [])],
            enabled=bool(server.get("enabled", True)),
        )
    except (GoRuntimeUnavailable, GoRuntimeError, OSError, RuntimeError):
        return None


def _go_mcp_tools(server_id: str, server: dict[str, Any], workspace: Path) -> dict[str, Any] | None:
    if _MCP_GRPC_AVAILABLE:
        try:
            result = _mcp_grpc.list_mcp_tools(server_id)
            if isinstance(result, dict) and result.get("ok"):
                result.setdefault("transport", "go_grpc")
                return result
        except Exception:
            pass
    if _go_mcp_probe(server_id, server, workspace) is None:
        return None
    try:
        result = go_mcp_gateway_client.list_mcp_tools(server_id)
    except (GoRuntimeUnavailable, GoRuntimeError, OSError, RuntimeError):
        return None
    if not isinstance(result, dict):
        return None
    result.setdefault("transport", "go_stdio")
    return result


def _go_mcp_call(
    server_id: str,
    server: dict[str, Any],
    workspace: Path,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    run_id: str = "",
    permission_level: str = "",
    requires_approval: bool = False,
    approval_id: str = "",
    approval_token: str = "",
) -> dict[str, Any] | None:
    if _MCP_GRPC_AVAILABLE:
        try:
            result = _mcp_grpc.call_mcp_tool(
                server_id,
                tool_name,
                arguments,
                workspace_dir=str(workspace),
                permission_level=permission_level,
                requires_approval=requires_approval,
                approval_id=approval_id,
                approval_token=approval_token,
                run_id=run_id,
            )
            if isinstance(result, dict):
                result.setdefault("transport", "go_grpc")
                return result
        except Exception:
            pass
    if _go_mcp_probe(server_id, server, workspace) is None:
        return None
    try:
        result = go_mcp_gateway_client.call_mcp_tool(
            server_id,
            tool_name,
            arguments,
            run_id=run_id,
            workspace_dir=str(workspace),
            permission_level=permission_level,
            requires_approval=requires_approval,
            approval_id=approval_id,
            approval_token=approval_token,
        )
    except (GoRuntimeUnavailable, GoRuntimeError, OSError, RuntimeError):
        return None
    if not isinstance(result, dict):
        return None
    result.setdefault("transport", "go_stdio")
    return result


def probe_mcp_server(
    server_id: str,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Run static / semi-dynamic diagnostics on one MCP server.

    Returns a dict with ``status`` (passed/warning/failed) and ``checks`` list.
    """
    server = _mcp_server_config(server_id, workspace_dir)
    ws = workspace_dir or config_module.WORKSPACE_DIR
    checks: list[dict[str, Any]] = []

    if server is None:
        return {
            "server_id": server_id,
            "status": "failed",
            "checks": [{"id": "server_not_found", "label": "Server 查找",
                        "status": "failed", "detail": f"未找到 MCP server: {server_id}"}],
        }

    go_probe = _go_mcp_probe(server_id, server, Path(ws).resolve())
    if go_probe is not None:
        checks = [
            {
                "id": str(item.get("id") or ""),
                "label": str(item.get("id") or ""),
                "status": str(item.get("status") or "warning"),
                "detail": str(item.get("message") or ""),
            }
            for item in go_probe.get("checks", [])
            if isinstance(item, dict)
        ]
        return {
            "server_id": server_id,
            "status": go_probe.get("status", "warning"),
            "checks": checks,
            "server": server,
            "transport": "go_stdio",
        }

    # 1. Server enabled
    enabled = server.get("enabled", True)
    if enabled is False:
        checks.append({"id": "enabled", "label": "Server 已启用",
                       "status": "failed", "detail": "该 MCP server 已被禁用。"})
        return {
            "server_id": server_id,
            "status": "failed",
            "checks": checks,
            "server": server,
        }
    checks.append({"id": "enabled", "label": "Server 已启用",
                   "status": "passed", "detail": "已启用。"})

    # 2. Command exists on PATH
    command = server.get("command", "")
    if command:
        found = shutil.which(command) is not None
        checks.append({
            "id": "command_on_path",
            "label": f"命令可执行: {command}",
            "status": "passed" if found else "warning",
            "detail": f"{command} 在 PATH 中。" if found else f"{command} 未在 PATH 中找到。请确认已安装。",
        })
    else:
        checks.append({
            "id": "command_on_path",
            "label": "命令可执行",
            "status": "warning",
            "detail": "未声明 command。",
        })

    # 3. Env keys present
    for key in server.get("env_keys", []):
        present = bool(os.environ.get(key))
        checks.append({
            "id": f"env_{key}",
            "label": f"环境变量: {key}",
            "status": "passed" if present else "warning",
            "detail": f"{key} 已设置。" if present else f"{key} 未设置。请在 .env 中添加。",
        })

    # 4. Config parseable
    try:
        json.dumps(server)
        checks.append({
            "id": "config_valid",
            "label": "配置格式",
            "status": "passed",
            "detail": "配置 JSON 格式正确。",
        })
    except (TypeError, ValueError):
        checks.append({
            "id": "config_valid",
            "label": "配置格式",
            "status": "failed",
            "detail": "配置 JSON 序列化失败。",
        })

    # Overall status
    statuses = [c["status"] for c in checks]
    if any(s == "failed" for s in statuses):
        overall = "failed"
    elif any(s == "warning" for s in statuses):
        overall = "warning"
    else:
        overall = "passed"

    return {
        "server_id": server_id,
        "status": overall,
        "checks": checks,
        "server": server,
    }


def list_mcp_tools(
    server_id: str,
    workspace_dir: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """List tools exposed by an MCP stdio server."""
    workspace = _workspace(workspace_dir)
    status = get_mcp_server_status(server_id, str(workspace))
    circuit_remaining = _circuit_remaining_seconds(status)
    server = _mcp_server_config(server_id, str(workspace))
    if server is None:
        _record_mcp_failure(server_id, str(workspace), f"未找到 MCP server: {server_id}")
        return {"server_id": server_id, "tools": [], "error": f"未找到 MCP server: {server_id}"}

    fingerprint = _server_fingerprint(server)
    if circuit_remaining:
        cached = _cached_tools(status, fingerprint, allow_stale=True)
        error = f"MCP server 熔断中，请 {circuit_remaining} 秒后重试。"
        return {
            "server_id": server_id,
            "command": server.get("command", ""),
            "tools": cached["tools"] if cached else [],
            "status": "circuit_open",
            "ok": False,
            "error": error,
            "cache": cached["cache"] if cached else "miss",
            "circuit_remaining_seconds": circuit_remaining,
            "fallback": _fallback_payload(
                used=bool(cached),
                reason=error,
                strategy="stale_tool_catalog" if cached else "none",
                can_continue=bool(cached),
                recommended_action=(
                    "继续使用上一次成功发现的 MCP 工具目录；真正调用前仍需重新检查 server 状态。"
                    if cached else
                    "跳过该 MCP server，继续使用本地工具或其他可用 MCP server。"
                ),
                source="tools_cache" if cached else "runtime_policy",
            ),
        }

    cached = None if force_refresh else _cached_tools(status, fingerprint)
    if cached:
        return {
            "server_id": server_id,
            "command": server.get("command", ""),
            "tools": cached["tools"],
            "status": status.get("status", "ready"),
            "ok": True,
            "transport": "stdio",
            "cache": cached["cache"],
            "cached_at": cached["cached_at"],
        }

    go_result = _go_mcp_tools(server_id, server, workspace)
    if go_result is not None and go_result.get("ok"):
        tools = go_result.get("tools") if isinstance(go_result.get("tools"), list) else []
        cached_at = time.time()
        _record_mcp_success(
            server_id,
            str(workspace),
            {
                "tools_cache": {
                    "tools": tools,
                    "cached_at": cached_at,
                    "config_hash": fingerprint,
                },
                "last_tools_count": len(tools),
            },
        )
        return {
            "server_id": server_id,
            "command": server.get("command", ""),
            "tools": tools,
            "status": go_result.get("status", "ready"),
            "ok": True,
            "transport": "go_stdio",
            "cache": "miss",
            "cached_at": cached_at,
        }

    probe = probe_mcp_server(server_id, workspace_dir)
    if probe["status"] == "failed":
        error = probe["checks"][0]["detail"]
        updated = _record_mcp_failure(server_id, str(workspace), error)
        fallback = _tool_discovery_fallback(server_id, server, updated, fingerprint, error)
        if fallback:
            return fallback
        return {
            "server_id": server_id,
            "tools": [],
            "status": "failed",
            "ok": False,
            "error": error,
            "cache": "miss",
            "fallback": _fallback_payload(
                used=False,
                reason=error,
                strategy="none",
                can_continue=True,
                recommended_action="跳过该 MCP server，继续使用本地工具或其他可用 MCP server。",
                source="runtime_policy",
            ),
        }

    server = probe.get("server", {})
    try:
        response = _run_mcp_request(server, workspace, "tools/list", {})
    except Exception as exc:
        error = str(exc)
        updated = _record_mcp_failure(server_id, str(workspace), error)
        fallback = _tool_discovery_fallback(server_id, server, updated, fingerprint, error)
        if fallback:
            return fallback
        return {
            "server_id": server_id,
            "command": server.get("command", ""),
            "tools": [],
            "status": "failed",
            "ok": False,
            "error": error,
            "cache": "miss",
            "fallback": _fallback_payload(
                used=False,
                reason=error,
                strategy="none",
                can_continue=True,
                recommended_action="跳过该 MCP server，继续使用本地工具或其他可用 MCP server。",
                source="runtime_policy",
            ),
        }

    if response.get("error"):
        error = _normalise_mcp_error(response.get("error"))
        updated = _record_mcp_failure(server_id, str(workspace), error)
        fallback = _tool_discovery_fallback(server_id, server, updated, fingerprint, error)
        if fallback:
            fallback["stderr"] = response.get("stderr", "")
            return fallback
        return {
            "server_id": server_id,
            "command": server.get("command", ""),
            "tools": [],
            "status": "failed",
            "ok": False,
            "error": error,
            "stderr": response.get("stderr", ""),
            "cache": "miss",
            "fallback": _fallback_payload(
                used=False,
                reason=error,
                strategy="none",
                can_continue=True,
                recommended_action="跳过该 MCP server，继续使用本地工具或其他可用 MCP server。",
                source="runtime_policy",
            ),
        }

    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    tools = result.get("tools", []) if isinstance(result, dict) else []
    tools = tools if isinstance(tools, list) else []
    cached_at = time.time()
    _record_mcp_success(
        server_id,
        str(workspace),
        {
            "tools_cache": {
                "tools": tools,
                "cached_at": cached_at,
                "config_hash": fingerprint,
            },
            "last_tools_count": len(tools),
        },
    )
    return {
        "server_id": server_id,
        "command": server.get("command", ""),
        "tools": tools,
        "status": probe["status"],
        "ok": True,
        "transport": "stdio",
        "stderr": response.get("stderr", ""),
        "cache": "miss",
        "cached_at": cached_at,
    }


def list_all_mcp_tools(
    workspace_dir: str | None = None,
    *,
    force_refresh: bool = False,
    include_disabled: bool = False,
) -> dict[str, Any]:
    """List all MCP tools from configured servers with permission metadata."""
    workspace = _workspace(workspace_dir)
    servers = list_mcp_servers(str(workspace)).get("servers", [])
    server_results: list[dict[str, Any]] = []
    for server in servers:
        if server.get("status") != "configured":
            continue
        if not include_disabled and server.get("enabled") is False:
            server_results.append({
                "server_id": server.get("id", ""),
                "status": "disabled",
                "ok": False,
                "tools": [],
                "error": "MCP server 已禁用。",
                "cache": "skip",
            })
            continue
        server_results.append(
            list_mcp_tools(
                str(server.get("id", "")),
                str(workspace),
                force_refresh=force_refresh,
            )
        )
    catalog = build_mcp_tool_catalog(server_results)
    return {
        "workspace_dir": str(workspace),
        "catalog": catalog["tools"],
        "servers": catalog["servers"],
        "summary": catalog["summary"],
    }


def call_mcp_tool(
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    workspace_dir: str | None = None,
    run_id: str = "",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    permission_level: str = "",
    requires_approval: bool = False,
    approval_id: str = "",
    approval_token: str = "",
) -> dict[str, Any]:
    """Call an MCP tool through stdio JSON-RPC."""
    workspace = _workspace(workspace_dir)
    status = get_mcp_server_status(server_id, str(workspace))
    circuit_remaining = _circuit_remaining_seconds(status)
    if circuit_remaining:
        error = f"MCP server 熔断中，请 {circuit_remaining} 秒后重试。"
        return {
            "server_id": server_id,
            "tool": tool_name,
            "arguments": arguments or {},
            "ok": False,
            "error": error,
            "transport": "stdio",
            "status": "circuit_open",
            "circuit_remaining_seconds": circuit_remaining,
            "fallback": _call_failure_fallback(server_id, tool_name, error),
        }

    probe = probe_mcp_server(server_id, workspace_dir)
    if probe["status"] == "failed":
        error = "MCP server 不可用。"
        _record_mcp_failure(server_id, str(workspace), error)
        return {"server_id": server_id, "tool": tool_name, "ok": False,
                "error": error,
                "fallback": _call_failure_fallback(server_id, tool_name, error)}

    disabled_check = next((c for c in probe["checks"] if c["id"] == "enabled" and c["status"] == "failed"), None)
    if disabled_check:
        error = "MCP server 已被禁用，无法调用工具。"
        _record_mcp_failure(server_id, str(workspace), error)
        return {"server_id": server_id, "tool": tool_name, "ok": False,
                "error": error,
                "fallback": _call_failure_fallback(server_id, tool_name, error)}

    server = probe.get("server", {})
    go_result = _go_mcp_call(
        server_id,
        server,
        workspace,
        tool_name,
        arguments or {},
        run_id=run_id,
        permission_level=permission_level,
        requires_approval=requires_approval,
        approval_id=approval_id,
        approval_token=approval_token,
    )
    if go_result is not None:
        if go_result.get("ok"):
            _record_mcp_success(server_id, str(workspace), {"last_tool": tool_name})
            record_mcp_usage(server_id, "", str(workspace))
            return {
                "server_id": server_id,
                "tool": tool_name,
                "arguments": arguments or {},
                "ok": True,
                "result": go_result.get("result", {}),
                "transport": "go_stdio",
            }
        error = str(go_result.get("error") or "MCP tool call failed")
        error_code = str(go_result.get("error_code") or "")
        if go_result.get("status") == "denied" or error_code in {"approval_required", "approval_invalid"}:
            return {
                "server_id": server_id,
                "tool": tool_name,
                "arguments": arguments or {},
                "ok": False,
                "error": error,
                "error_code": error_code,
                "status": "denied",
                "transport": "go_stdio",
                "permission_level": go_result.get("permission_level", permission_level),
                "requires_approval": bool(go_result.get("requires_approval", requires_approval)),
                "fallback": _fallback_payload(
                    used=False,
                    reason=error,
                    strategy="approval_required",
                    can_continue=False,
                    recommended_action="该 MCP 工具需要用户审批；审批通过后会携带短期 approval token 重新调用。",
                    source="runtime_policy",
                ),
            }
        _record_mcp_failure(server_id, str(workspace), error)
        return {
            "server_id": server_id,
            "tool": tool_name,
            "arguments": arguments or {},
            "ok": False,
            "error": error,
            "transport": "go_stdio",
            "fallback": _call_failure_fallback(server_id, tool_name, error),
        }
    try:
        response = _run_mcp_request(
            server,
            workspace,
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        error = str(exc)
        _record_mcp_failure(server_id, str(workspace), error)
        return {
            "server_id": server_id,
            "tool": tool_name,
            "arguments": arguments or {},
            "ok": False,
            "error": error,
            "transport": "stdio",
            "fallback": _call_failure_fallback(server_id, tool_name, error),
        }

    if response.get("error"):
        error = _normalise_mcp_error(response.get("error"))
        _record_mcp_failure(server_id, str(workspace), error)
        return {
            "server_id": server_id,
            "tool": tool_name,
            "arguments": arguments or {},
            "ok": False,
            "error": error,
            "stderr": response.get("stderr", ""),
            "transport": "stdio",
            "fallback": _call_failure_fallback(server_id, tool_name, error),
        }

    _record_mcp_success(server_id, str(workspace), {"last_tool": tool_name})
    record_mcp_usage(server_id, "", str(workspace))
    return {
        "server_id": server_id,
        "tool": tool_name,
        "arguments": arguments or {},
        "ok": True,
        "result": response.get("result", {}),
        "stderr": response.get("stderr", ""),
        "transport": "stdio",
    }
