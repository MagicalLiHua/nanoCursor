"""System routes: version, paths, doctor."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import APIRouter

from src.infra import config as config_module

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/version")
async def system_version():
    return {"version": "0.1.0", "commit": "dev", "api_version": "1"}


@router.get("/paths")
async def system_paths():
    return {
        "project_root": config_module.PROJECT_ROOT,
        "runtime_root": config_module.RUNTIME_ROOT,
        "workspace_root": getattr(config_module, "WORKSPACE_ROOT", ""),
        "workspace_dir": config_module.WORKSPACE_DIR,
    }


@router.get("/doctor")
async def system_doctor(workspace_dir: str | None = None):
    checks: list[dict] = []
    ws = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()

    checks.append({"id": "python", "status": "passed",
                   "message": f"Python {sys.version_info.major}.{sys.version_info.minor}"})

    writable = os.access(ws, os.W_OK) if ws.exists() else False
    checks.append({"id": "workspace", "status": "passed" if writable else "warning",
                   "message": "workspace writable" if writable else "workspace not writable",
                   "path": str(ws)})

    try:
        from src.api.services.migration_service import inspect_workspace_migrations
        migration = inspect_workspace_migrations(str(ws))
        checks.append({
            "id": "workspace_migration",
            "status": "passed" if not migration["actions"] else "warning",
            "message": "workspace metadata up to date"
            if not migration["actions"]
            else f"pending migrations: {', '.join(migration['actions'])}",
        })
    except Exception as exc:
        checks.append({
            "id": "workspace_migration",
            "status": "warning",
            "message": f"migration inspection failed: {exc}",
        })

    checks.append({"id": "env", "status": "passed" if os.path.exists(".env") else "warning",
                   "message": ".env present" if os.path.exists(".env") else ".env missing"})

    llm_found = any(os.environ.get(k) for k in
                    ["ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "MINIMAX_API_KEY", "OLLAMA_BASE_URL"])
    checks.append({"id": "llm", "status": "passed" if llm_found else "warning",
                   "message": "LLM configured" if llm_found else "no LLM key found"})

    issues = sum(1 for c in checks if c["status"] != "passed")
    return {"ok": issues == 0, "workspace_dir": str(ws), "checks": checks}


@router.get("/diagnostics")
async def system_diagnostics(workspace_dir: str | None = None):
    """Build a full diagnostic bundle (no API keys exposed)."""
    from src.api.services.diagnostic_service import build_diagnostic_bundle
    return build_diagnostic_bundle(workspace_dir)
