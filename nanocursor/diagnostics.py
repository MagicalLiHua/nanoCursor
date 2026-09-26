"""Read-only diagnostics; network testing is explicitly requested."""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import shutil
import stat
import sys
from pathlib import Path

from nanocursor.config import load_config
from nanocursor.runtime import app_home, get_version, redact
from nanocursor.validator import ConfigError
from nanocursor.workspace import WorkspaceContext


def diagnose(workspace: WorkspaceContext, *, network: bool = False, env_provider: str | None = None) -> dict:
    checks: list[dict] = []

    def add(name, ok, message, *, warning=False):
        checks.append({"name": name, "status": "ok" if ok else "warning" if warning else "error",
                       "message": redact(str(message))})

    executables = []
    for folder in os.get_exec_path():
        candidate = Path(folder) / ("nanocursor.exe" if os.name == "nt" else "nanocursor")
        if candidate.is_file() and os.access(candidate, os.X_OK) and str(candidate) not in executables:
            executables.append(str(candidate))
    try:
        dist = importlib.metadata.distribution("nanocursor")
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
        source = "editable" if direct.get("dir_info", {}).get("editable") else "package"
    except (importlib.metadata.PackageNotFoundError, ValueError):
        source = "source/unknown"
    add("installation", True, f"{source}; Python {sys.version.split()[0]}; {sys.executable}")
    add("command", bool(executables), ", ".join(executables) or "Not on PATH; use uv tool update-shell and reopen the terminal", warning=True)
    if len(executables) > 1:
        add("multiple_commands", False, "Several commands found; the first PATH entry wins", warning=True)
    add("workspace", True, workspace.active_cwd)
    add("app_home", True, app_home())
    add("git", bool(shutil.which("git")), shutil.which("git") or "Git unavailable; Worktree features require Git", warning=True)
    shell = shutil.which("sh") if os.name == "posix" else os.environ.get("COMSPEC")
    add("shell", bool(shell), shell or "No command shell found")
    try:
        old = workspace.previous_worktree()
        if old:
            add("previous_worktree", True, f"Available, not automatically restored: {old.worktree_path}")
        config = load_config(work_dir=workspace.workspace_dir, env_provider=env_provider)
        add("config", True, json.dumps(config.sources, ensure_ascii=False))
        from nanocursor.hooks import load_hooks, HookConfigError
        try:
            load_hooks(config.raw_hooks)
            add("hooks", True, "Definitions validated; not executed")
        except HookConfigError:
            add("hooks", False, "Invalid hook definitions; check hook configuration fields")
        add("project_trust", config.project_trusted, "Approved" if config.project_trusted else "Project settings require review; run nanocursor interactively")
        provider = config.selected_provider
        add("provider", True, f"{provider.name}; {provider.protocol}; {provider.base_url}; {provider.model}")
        try:
            exists = bool(provider.resolve_api_key())
            source = "environment: " + provider.api_key_env if provider.api_key_env else "credential file" if provider.credential_ref else "no authentication" if provider.auth == "none" else "legacy config/environment"
            add("credential", exists, source + (" (present, not validated)" if exists else " (missing; run nanocursor setup)"))
        except (ConfigError, OSError) as exc:
            exists = False
            add("credential", False, str(exc))
        if config.sandbox.enabled:
            from nanocursor.sandbox import create_sandbox
            sandbox = create_sandbox()
            available = bool(sandbox and sandbox.available())
            add("sandbox", available, type(sandbox).__name__ if available else "Configured backend unavailable; startup requires the backend or an explicitly disabled sandbox")
        else:
            add("sandbox", True, "Disabled; commands use process permissions")
        for server in config.mcp_servers:
            if server.command:
                add("mcp:" + server.name, bool(shutil.which(server.command)), "Executable available" if shutil.which(server.command) else "Configured executable missing", warning=True)
        if network:
            if config.project_trusted and exists:
                from nanocursor.connection import check_connection
                result = asyncio.run(check_connection(provider))
                add("network", result.ok, f"{result.code}: {result.message}")
            else:
                add("network", False, "Skipped: approve project settings and configure credentials first")
    except (ConfigError, OSError, ValueError, TypeError) as exc:
        add("config", False, str(exc))
    for path in (app_home(), app_home() / "credentials.json", app_home() / "config.yaml"):
        if path.is_symlink():
            add("storage", False, f"Symbolic link requires manual review: {path}")
        elif path.exists() and os.name == "posix":
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                add("storage_permissions", False, f"{path}: permissions {mode:o}; restrict access to your user", warning=True)
    return {"version": get_version(), "ok": not any(c["status"] == "error" for c in checks), "checks": checks}


def run_doctor(workspace: WorkspaceContext, *, json_output: bool = False, network: bool = False,
               env_provider: str | None = None) -> int:
    result = diagnose(workspace, network=network, env_provider=env_provider)
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"nanoCursor {result['version']}")
        for check in result["checks"]:
            print(f"{check['status'].upper():7} {check['name']}: {check['message']}")
    return 0 if result["ok"] else 1
