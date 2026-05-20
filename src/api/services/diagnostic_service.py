"""System diagnostic bundle — workspace, settings, runs, MCP, skills, errors.

Never leaks API keys or sensitive file contents.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.api.services.mcp_service import list_mcp_servers
from src.api.services.eval_service import build_aggregate_metrics


_SENSITIVE_ENV_PREFIXES = (
    "API_KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH",
)


def _env_safe_report() -> dict[str, Any]:
    """Return env summary: keys grouped by category, values always masked."""
    env_keys: dict[str, list[str]] = {
        "llm": [], "system": [], "other": [],
    }
    llm_prefixes = ("ANTHROPIC", "OPENAI", "DEEPSEEK", "MINIMAX", "OLLAMA", "LLM")

    for key in sorted(os.environ.keys()):
        if any(key.startswith(p) for p in llm_prefixes):
            env_keys["llm"].append(key)
        elif key.startswith(("NANOCURSOR", "SANDBOX", "LOG", "PATH", "PYTHON")):
            env_keys["system"].append(key)
        else:
            env_keys["other"].append(key)

    sensitive = set()
    for key in os.environ:
        if any(prefix in key.upper() for prefix in _SENSITIVE_ENV_PREFIXES):
            sensitive.add(key)

    return {
        "total_keys": len(os.environ),
        "by_category": {k: len(v) for k, v in env_keys.items()},
        "llm_providers": [
            {"key": k, "present": bool(os.environ.get(k))}
            for k in env_keys["llm"]
        ],
        "sensitive_keys_present": sorted(sensitive),
        "note": "敏感值不会暴露。llm_providers 仅报告 key 是否存在。",
    }


def _workspace_report() -> dict[str, Any]:
    ws = Path(config_module.WORKSPACE_DIR).resolve()
    info: dict[str, Any] = {
        "path": str(ws),
        "exists": ws.exists(),
        "is_dir": ws.is_dir() if ws.exists() else False,
    }
    if ws.is_dir():
        try:
            entries = list(ws.iterdir())
            info["entry_count"] = len(entries)
            info["top_level"] = sorted(
                e.name for e in entries[:30]
                if not e.name.startswith(".")
            )
        except OSError:
            pass
    return info


def _settings_report() -> dict[str, Any]:
    try:
        from src.api.services.workspace_settings_service import get_effective_settings
        return get_effective_settings(str(config_module.WORKSPACE_DIR))
    except Exception:
        return {}


def _runs_report() -> dict[str, Any]:
    metrics = build_aggregate_metrics()
    runs_dir = Path(config_module.WORKSPACE_DIR) / ".nanocursor" / "runs"
    recent: list[dict[str, Any]] = []
    if runs_dir.exists():
        for d in sorted(runs_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)[:5]:
            if not d.is_dir():
                continue
            sf = d / "session.json"
            if sf.exists():
                try:
                    s = json.loads(sf.read_text(encoding="utf-8"))
                    recent.append({
                        "thread_id": s.get("thread_id", d.name),
                        "status": s.get("status", "unknown"),
                        "prompt": (s.get("prompt", "") or "")[:100],
                    })
                except (json.JSONDecodeError, OSError):
                    pass
    return {"metrics": metrics, "recent_runs": recent}


def _mcp_report() -> dict[str, Any]:
    try:
        data = list_mcp_servers()
        servers = data.get("servers", [])
        return {
            "total": data.get("summary", {}).get("total", 0),
            "configured": data.get("summary", {}).get("configured", 0),
            "servers": [
                {"id": s["id"], "status": s["status"], "command": s.get("command", "")[:80]}
                for s in servers if s.get("status") != "planned"
            ],
        }
    except Exception:
        return {}


def _skills_report() -> dict[str, Any]:
    ws = Path(config_module.WORKSPACE_DIR)
    skills_dir = ws / ".nanocursor" / "skills"
    if not skills_dir.exists():
        return {"count": 0, "skills": []}

    skills: list[dict[str, Any]] = []
    for sd in sorted(skills_dir.iterdir()):
        if not sd.is_dir():
            continue
        sm = sd / "SKILL.md"
        if sm.exists():
            skills.append({"id": sd.name, "size": sm.stat().st_size})
    return {"count": len(skills), "skills": skills}


def _errors_report() -> dict[str, Any]:
    """Scan recent runs for error events."""
    runs_dir = Path(config_module.WORKSPACE_DIR) / ".nanocursor" / "runs"
    errors: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return {"error_count": 0, "recent_errors": []}

    for d in sorted(runs_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)[:10]:
        if not d.is_dir():
            continue
        ef = d / "events.jsonl"
        if not ef.exists():
            continue
        try:
            for line in ef.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("type") == "error":
                    errors.append({
                        "thread_id": ev.get("thread_id", d.name),
                        "title": ev.get("title", ""),
                        "content": (ev.get("content", "") or "")[:200],
                    })
        except (json.JSONDecodeError, OSError):
            continue
        if len(errors) >= 10:
            break

    return {"error_count": len(errors), "recent_errors": errors[:10]}


def build_diagnostic_bundle(workspace_dir: str | None = None) -> dict[str, Any]:
    """Build a full diagnostic bundle for the current workspace.

    Safe to expose via API — never contains API key values.
    """
    return {
        "generated_at": time.time(),
        "system": {
            "platform": platform.platform(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "pid": os.getpid(),
            "cwd": os.getcwd(),
        },
        "workspace": _workspace_report(),
        "settings": _settings_report(),
        "runs": _runs_report(),
        "mcp": _mcp_report(),
        "skills": _skills_report(),
        "errors": _errors_report(),
        "env": _env_safe_report(),
    }
