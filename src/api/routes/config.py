"""Config, metrics, files, snapshots, backups routes."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

import src.infra.config as config_module
from src.infra.metrics import metrics as metrics_collector
from src.infra.path_guard import resolve_workspace_path
from src.runtime.command_runner import run_command_async
from src.api.models import (
    BackupContentResponse,
    BackupEntry,
    BackupListResponse,
    CodeFile,
    ConfigResponse,
    EnvVar,
    FileContentResponse,
    FileEntry,
    FileListResponse,
    LLMProviderStatus,
    MetricsCurrentResponse,
    MetricsLLMData,
    MetricsRepairData,
    MetricsResponse,
    MetricsToolData,
    SnapshotDetailResponse,
    SnapshotEntry,
    SnapshotListResponse,
    SnapshotMetadata,
    SystemConfig,
)

router = APIRouter(tags=["config"])

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
METRICS_HISTORY_FILE = os.path.join(ROOT, "metrics_history.json")


# --- Files ---

@router.get("/api/files")
async def list_files():
    files = []
    try:
        for root, dirs, filenames in os.walk(config_module.WORKSPACE_DIR):
            dirs[:] = [d for d in dirs if d not in (".backups", ".snapshots")]
            for filename in filenames:
                filepath = os.path.join(root, filename)
                relpath = os.path.relpath(filepath, config_module.WORKSPACE_DIR)
                try:
                    stat = os.stat(filepath)
                    files.append({
                        "path": relpath,
                        "is_dir": False,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    })
                except OSError:
                    pass
            for dirname in dirs:
                dirpath = os.path.join(root, dirname)
                relpath = os.path.relpath(dirpath, config_module.WORKSPACE_DIR)
                files.append({
                    "path": relpath,
                    "is_dir": True,
                    "size": 0,
                })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取工作区失败: {e!s}")

    files.sort(key=lambda f: f["path"])
    return FileListResponse(files=[
        FileEntry(path=f["path"], is_dir=f["is_dir"], size=f["size"], mtime=f.get("mtime"))
        for f in files
    ])


@router.get("/api/files/{file_path:path}")
async def read_file(file_path: str):
    full_path = os.path.join(config_module.WORKSPACE_DIR, file_path)

    real_path = os.path.realpath(full_path)
    real_root = os.path.realpath(config_module.WORKSPACE_DIR)
    if os.path.commonpath([real_root, real_path]) != real_root:
        raise HTTPException(status_code=403, detail="禁止访问该路径")

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    if os.path.isdir(full_path):
        raise HTTPException(status_code=400, detail="这是一个目录，不是文件")

    try:
        stat = os.stat(full_path)
        try:
            with open(full_path, encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            content = "[二进制文件，无法显示内容]"

        ext = os.path.splitext(file_path)[1].lower()
        lang_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".tsx": "typescript", ".jsx": "javascript", ".html": "html",
            ".css": "css", ".json": "json", ".md": "markdown",
            ".txt": "text", ".yaml": "yaml", ".yml": "yaml",
            ".sh": "bash", ".go": "go", ".java": "java",
            ".c": "c", ".cpp": "cpp", ".rs": "rust",
        }
        lang = lang_map.get(ext, "text")

        return FileContentResponse(
            content=content,
            size=stat.st_size,
            lines=content.count("\n") + 1,
            mtime=stat.st_mtime,
            lang=lang,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {e!s}")


# --- Metrics ---

@router.get("/api/metrics")
async def get_metrics():
    summary = metrics_collector.dump_summary()
    llm_data = summary.get("llm", {})
    tool_data = summary.get("tool_calls", {})
    repair_data = summary.get("repair_cycles", {})

    current = MetricsCurrentResponse(
        total_llm_calls=llm_data.get("total_calls", 0),
        total_tokens=llm_data.get("total_tokens", 0),
        llm_latency_avg=llm_data.get("avg_latency_ms", 0.0),
        tool_calls=tool_data.get("total", 0),
        tool_successes=tool_data.get("successes", 0),
        tool_failures=tool_data.get("failures", 0),
        tool_success_rate=tool_data.get("success_rate", 0.0),
        repair_cycles=repair_data.get("total", 0),
        repair_cycles_recovered=sum(1 for o in repair_data.get("outcomes", []) if o.get("outcome") == "fixed"),
        last_updated=None,
        llm=MetricsLLMData(
            total_calls=llm_data.get("total_calls", 0),
            total_tokens=llm_data.get("total_tokens", 0),
            avg_tokens_per_call=llm_data.get("avg_tokens_per_call", 0.0),
            avg_latency_ms=llm_data.get("avg_latency_ms", 0.0),
            max_latency_ms=llm_data.get("max_latency_ms", 0.0),
            min_latency_ms=llm_data.get("min_latency_ms", 0.0),
        ),
        tool_calls_detail=MetricsToolData(
            total=tool_data.get("total", 0),
            successes=tool_data.get("successes", 0),
            failures=tool_data.get("failures", 0),
            success_rate=tool_data.get("success_rate", 0.0),
            failure_reasons=tool_data.get("failure_reasons", []),
        ),
        repair_cycles_detail=MetricsRepairData(
            total=repair_data.get("total", 0),
            outcomes=repair_data.get("outcomes", []),
        ),
    )

    historical = []
    if os.path.exists(METRICS_HISTORY_FILE):
        try:
            with open(METRICS_HISTORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                historical = data
        except Exception:
            pass

    return MetricsResponse(current=current, historical=historical)


# --- Config ---

async def _check_ollama_connected(base_url: str, timeout: float = 2.0) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


@router.get("/api/config")
async def get_config():
    try:
        from src.api.services.workspace_runtime_service import get_active_workspace
        from src.api.services.workspace_settings_service import get_effective_model_settings

        model_override = get_effective_model_settings(workspace_dir=get_active_workspace())
    except Exception:
        model_override = {}
    override_provider = str(model_override.get("provider") or "").strip().lower()
    override_model = str(model_override.get("model") or "").strip()
    override_base_url = str(model_override.get("base_url") or "").strip()
    override_has_key = bool(str(model_override.get("api_key") or "").strip())

    def _provider_has_key(provider: str, env_key: str) -> bool:
        return bool(os.getenv(env_key)) or (override_provider == provider and override_has_key)

    def _provider_model(provider: str, env_key: str, default: str) -> str:
        return override_model if override_provider == provider and override_model else os.getenv(env_key, default)

    def _provider_base(provider: str, env_key: str, default: str | None = None) -> str | None:
        return override_base_url if override_provider == provider and override_base_url else (os.getenv(env_key) or default)

    llm_providers = {
        "openai": LLMProviderStatus(
            has_key=_provider_has_key("openai", "OPENAI_API_KEY"),
            model=_provider_model("openai", "OPENAI_MODEL", "gpt-4o"),
            base_url=_provider_base("openai", "OPENAI_API_BASE") or _provider_base("openai", "OPENAI_BASE_URL"),
        ),
        "anthropic": LLMProviderStatus(
            has_key=_provider_has_key("anthropic", "ANTHROPIC_API_KEY"),
            model=_provider_model("anthropic", "ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
            base_url=_provider_base("anthropic", "ANTHROPIC_BASE_URL"),
        ),
        "ollama": LLMProviderStatus(
            has_key=True,
            model=_provider_model("ollama", "OLLAMA_MODEL", "qwen2.5-coder"),
            base_url=_provider_base("ollama", "OLLAMA_BASE_URL", "http://localhost:11434"),
            is_connected=await _check_ollama_connected(
                _provider_base("ollama", "OLLAMA_BASE_URL", "http://localhost:11434") or "http://localhost:11434"
            ),
        ),
        "deepseek": LLMProviderStatus(
            has_key=_provider_has_key("deepseek", "DEEPSEEK_API_KEY"),
            model=_provider_model("deepseek", "DEEPSEEK_MODEL", "deepseek-chat"),
            base_url=_provider_base("deepseek", "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        ),
        "minimax": LLMProviderStatus(
            has_key=_provider_has_key("minimax", "MINIMAX_API_KEY"),
            model=_provider_model("minimax", "MINIMAX_MODEL", "MiniMax-M2.7"),
            base_url=_provider_base("minimax", "MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic"),
        ),
    }

    system_config = SystemConfig(
        workspace_dir=str(config_module.WORKSPACE_DIR),
        sandbox_image=os.getenv("SANDBOX_IMAGE", "python:3.10-slim"),
        sandbox_mem_limit=os.getenv("SANDBOX_MEM_LIMIT", "256m"),
        sandbox_timeout=int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "60")),
        max_coder_steps=int(os.getenv("MAX_CODER_STEPS", "15")),
        max_planner_steps=int(os.getenv("MAX_PLANNER_STEPS", "10")),
        context_max_tokens=int(os.getenv("CONTEXT_MAX_TOKENS", "8000")),
    )

    env_vars = []
    sensitive_keys = {"key", "secret", "token", "password"}
    for key, value in sorted(os.environ.items()):
        is_sensitive = any(s in key.lower() for s in sensitive_keys)
        env_vars.append(EnvVar(
            name=key,
            value="****" if is_sensitive and value else value,
            is_sensitive=is_sensitive,
            is_set=True,
        ))

    return ConfigResponse(
        llm_providers=llm_providers,
        system=system_config,
        env_vars=env_vars,
    )


# --- Snapshots ---

@router.get("/api/snapshots")
async def list_snapshots():
    snapshots_dir = os.path.join(config_module.WORKSPACE_DIR, ".snapshots")
    snapshots = []
    if not os.path.exists(snapshots_dir):
        return SnapshotListResponse(snapshots=[])
    try:
        for entry in sorted(os.listdir(snapshots_dir), reverse=True):
            snapshot_path = os.path.join(snapshots_dir, entry)
            if not os.path.isdir(snapshot_path):
                continue
            metadata_path = os.path.join(snapshot_path, "metadata.json")
            metadata = {}
            if os.path.exists(metadata_path):
                try:
                    with open(metadata_path, encoding="utf-8") as f:
                        metadata = json.load(f)
                except Exception:
                    pass
            snapshots.append(SnapshotEntry(
                id=entry,
                timestamp=metadata.get("timestamp", ""),
                reason=metadata.get("reason", ""),
                active_files=metadata.get("active_files", []),
                active_files_count=len(metadata.get("active_files", [])),
            ))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取快照失败: {e!s}")
    return SnapshotListResponse(snapshots=snapshots)


@router.get("/api/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    snapshots_dir = Path(config_module.WORKSPACE_DIR).resolve() / ".snapshots"
    if not snapshots_dir.exists() or not snapshots_dir.is_dir():
        raise HTTPException(status_code=404, detail="快照不存在")

    try:
        snapshot_path_obj = resolve_workspace_path(snapshots_dir, snapshot_id, must_exist=True)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="快照不存在")

    if not snapshot_path_obj.is_dir():
        raise HTTPException(status_code=404, detail="快照不存在")
    snapshot_path = str(snapshot_path_obj)

    result = SnapshotDetailResponse(
        metadata=SnapshotMetadata(timestamp="", reason="", active_files=[]),
        conversation_summary="",
        code_files=[],
    )

    metadata_path = os.path.join(snapshot_path, "metadata.json")
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, encoding="utf-8") as f:
                metadata = json.load(f)
                result.metadata = SnapshotMetadata(
                    timestamp=metadata.get("timestamp", ""),
                    reason=metadata.get("reason", ""),
                    active_files=metadata.get("active_files", []),
                )
        except Exception:
            pass

    summary_path = os.path.join(snapshot_path, "conversation_summary.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path, encoding="utf-8") as f:
                result.conversation_summary = json.load(f)
        except Exception:
            pass

    code_dir = os.path.join(snapshot_path, "code")
    if os.path.exists(code_dir):
        for root, dirs, files in os.walk(code_dir):
            for filename in files:
                filepath = os.path.join(root, filename)
                relpath = os.path.relpath(filepath, code_dir)
                try:
                    with open(filepath, encoding="utf-8") as f:
                        content = f.read()
                    result.code_files.append(CodeFile(path=relpath, content=content))
                except Exception:
                    pass
    return result


# --- Backups ---

@router.get("/api/backups")
async def list_backups():
    backups_dir = os.path.join(config_module.WORKSPACE_DIR, ".backups")
    backups = []
    if not os.path.exists(backups_dir):
        return BackupListResponse(backups=[])
    try:
        for entry in os.listdir(backups_dir):
            filepath = os.path.join(backups_dir, entry)
            if not os.path.isfile(filepath):
                continue
            stat = os.stat(filepath)
            backups.append(BackupEntry(name=entry, size=stat.st_size, mtime=stat.st_mtime))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取备份失败: {e!s}")
    backups.sort(key=lambda b: b.mtime, reverse=True)
    return BackupListResponse(backups=backups)


@router.get("/api/backups/{backup_name}")
async def read_backup(backup_name: str):
    backups_dir = os.path.join(config_module.WORKSPACE_DIR, ".backups")
    filepath = os.path.join(backups_dir, backup_name)

    real_path = os.path.realpath(filepath)
    real_root = os.path.realpath(backups_dir)
    if not real_path.startswith(real_root):
        raise HTTPException(status_code=403, detail="禁止访问")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="备份文件不存在")

    try:
        stat = os.stat(filepath)
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        return BackupContentResponse(content=content, size=stat.st_size, mtime=stat.st_mtime)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取备份失败: {e!s}")


# --- Bash ---

from pydantic import BaseModel


class BashRequestModel(BaseModel):
    command: str
    workspace_dir: str | None = None
    timeout: int = 120


@router.post("/api/bash")
async def run_bash_command(request: BashRequestModel):
    command = request.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="命令不能为空")

    work_dir = request.workspace_dir or config_module.WORKSPACE_DIR
    work_dir = os.path.abspath(work_dir)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=400, detail=f"工作目录不存在: {work_dir}")

    dangerous = ["rm -rf /", "sudo ", "shutdown", "reboot", "> /dev/", "mkfs", "chroot", "dd if="]
    for pattern in dangerous:
        if pattern in command:
            return {"success": False, "stdout": "", "stderr": f"Error: Dangerous command blocked (matches '{pattern}')", "exit_code": -1}

    timeout = min(request.timeout, 300)
    result = await run_command_async(
        command,
        cwd=work_dir,
        timeout_seconds=timeout,
        max_stdout_chars=50_000,
        max_stderr_chars=10_000,
        permission_level="shell_safe",
    )
    return {
        "success": result.get("exit_code") == 0 and not result.get("timed_out"),
        "stdout": str(result.get("stdout") or "").strip() or "(no output)",
        "stderr": str(result.get("stderr") or "").strip(),
        "exit_code": result.get("exit_code", -1),
        "backend": result.get("backend"),
        "timed_out": result.get("timed_out", False),
    }
