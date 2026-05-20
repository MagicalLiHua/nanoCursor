#!/usr/bin/env python3
"""nanoCursor environment doctor — check if the system is ready to run."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def check(label: str) -> None:
    """Print a check line with status."""
    pass  # used in the run_checks helpers


def run_cmd(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.returncode, r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1, ""


def main() -> int:
    checks: list[dict] = []
    issues = 0

    def add(status: str, name: str, message: str) -> None:
        nonlocal issues
        symbol = {"pass": "✓", "warn": "!", "fail": "✗"}[status]
        print(f"  {symbol} {name}: {message}")
        checks.append({"id": name.lower().replace(" ", "_"), "status": status, "message": message})
        if status in ("warn", "fail"):
            issues += 1

    print("nanoCursor Doctor\n")

    # Python
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.version_info >= (3, 10):
        add("pass", "Python", py_ver)
    else:
        add("fail", "Python", f"{py_ver} (need >= 3.10)")

    # Node
    node_rc, node_ver = run_cmd(["node", "--version"])
    if node_rc == 0:
        add("pass", "Node", node_ver)
    else:
        add("warn", "Node", "not found (frontend dev server needs node)")

    # npm
    npm_rc, npm_ver = run_cmd(["npm", "--version"])
    if npm_rc == 0:
        add("pass", "npm", npm_ver)
    else:
        add("warn", "npm", "not found (frontend dev server needs npm)")

    # .env
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        add("pass", ".env", "present")
    else:
        add("warn", ".env", "missing, copy .env.example first")

    # Dependencies
    try:
        import pydantic, fastapi, uvicorn
        add("pass", "Dependencies", "core packages found")
    except ImportError as e:
        add("fail", "Dependencies", f"missing: {e}")

    # LLM provider
    llm_keys = ["ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                "MINIMAX_API_KEY", "OLLAMA_BASE_URL"]
    found_keys = [k for k in llm_keys if os.environ.get(k)]
    if found_keys:
        add("pass", "LLM Provider", f"detected: {', '.join(found_keys)}")
    else:
        add("warn", "LLM Provider", "no API key found, set one in .env")

    # Port availability
    def port_free(port: int) -> bool:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return True
        except OSError:
            return False

    ports_ok = True
    for port in [8100, 5173]:
        if not port_free(port):
            add("warn", f"Port {port}", "in use")
            ports_ok = False
    if ports_ok:
        add("pass", "Ports", "8100, 5173 available")

    # Git
    git_rc, git_ver = run_cmd(["git", "--version"])
    if git_rc == 0:
        add("pass", "Git", git_ver)
    else:
        add("warn", "Git", "not found")

    # Writeable workspace
    ws_dir = PROJECT_ROOT / ".nanocursor" / "workspaces" / "default"
    ws_dir.mkdir(parents=True, exist_ok=True)
    if os.access(ws_dir, os.W_OK):
        add("pass", "Workspace", f"writable ({ws_dir})")
    else:
        add("fail", "Workspace", f"not writable: {ws_dir}")

    # Playwright
    pw_browsers = Path.home() / "Library" / "Caches" / "ms-playwright"
    if pw_browsers.exists():
        add("pass", "Playwright", "browsers installed")
    else:
        add("warn", "Playwright", "browsers not installed (run: npx playwright install)")

    # Frontend node_modules
    node_modules = PROJECT_ROOT / "frontend" / "node_modules"
    if node_modules.is_dir():
        add("pass", "Frontend Deps", "node_modules found")
    else:
        add("warn", "Frontend Deps", "not installed (run: cd frontend && npm install)")

    # MCP config
    mcp_files = [".mcp.json", ".cursor/mcp.json", ".nanocursor/mcp.json"]
    mcp_found = any((PROJECT_ROOT / f).exists() for f in mcp_files)
    if mcp_found:
        add("pass", "MCP Config", "at least one mcp.json found")
    else:
        add("warn", "MCP Config", "no mcp.json found (optional)")

    # Summary
    print(f"\n  {len(checks)} checks, {issues} issues")
    if issues == 0:
        print("  All checks passed. Ready to run!\n")
        return 0
    else:
        print("  Fix issues above before running.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
