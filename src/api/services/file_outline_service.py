"""Persistent file outline cache for context packing."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any


def outline_cache_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir).resolve() / ".nanocursor" / "file_outlines.json"


def build_file_outlines_cache(
    workspace_dir: str | Path,
    index_data: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Build or refresh the workspace outline cache from project index data."""
    workspace = Path(workspace_dir).resolve()
    cache_path = outline_cache_path(workspace)
    entries = index_data.get("entries") if isinstance(index_data.get("entries"), dict) else {}
    existing = _read_cache(cache_path) if not force else _empty_cache()
    existing_outlines = existing.get("outlines") if isinstance(existing.get("outlines"), dict) else {}

    outlines: dict[str, Any] = {}
    changed = False
    for rel, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or rel)
        mtime = float(entry.get("mtime") or 0)
        cached = existing_outlines.get(path) if isinstance(existing_outlines, dict) else None
        if (
            isinstance(cached, dict)
            and not force
            and float(cached.get("mtime") or 0) >= mtime
        ):
            outlines[path] = cached
            continue
        outlines[path] = build_file_outline(workspace, path, entry)
        changed = True

    removed = set(existing_outlines) - set(outlines) if isinstance(existing_outlines, dict) else set()
    if removed:
        changed = True

    data = {
        "schema_version": 1,
        "workspace": str(workspace),
        "generated_at": time.time(),
        "outline_count": len(outlines),
        "outlines": outlines,
    }
    if changed or force or not cache_path.exists():
        _write_json_atomic(cache_path, data)
    return data


def load_file_outlines_cache(workspace_dir: str | Path) -> dict[str, Any]:
    return _read_cache(outline_cache_path(workspace_dir))


def build_file_outline(workspace: Path, rel_path: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Build one stable outline item from an index entry and lightweight file parsing."""
    path = workspace / rel_path
    language = str(entry.get("language") or "text")
    headings = _extract_headings(path, language)
    exports = _extract_exports(path, language)
    summary = _outline_summary(rel_path, entry, headings, exports)
    return {
        "path": rel_path,
        "role": entry.get("role", "source"),
        "language": language,
        "symbols": entry.get("symbols", [])[:20],
        "imports": entry.get("imports", [])[:20],
        "exports": exports[:20],
        "headings": headings[:20],
        "routes": entry.get("routes", [])[:20],
        "call_graph": entry.get("call_graph", {}),
        "loc": int(entry.get("loc", 0) or 0),
        "size": int(entry.get("size", 0) or 0),
        "mtime": float(entry.get("mtime", 0) or 0),
        "last_modified": float(entry.get("mtime", 0) or 0),
        "summary": summary,
    }


def select_cached_outlines(
    workspace_dir: str | Path,
    files: list[str],
    index_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return outlines for selected files, refreshing the cache if needed."""
    workspace = Path(workspace_dir).resolve()
    cache = load_file_outlines_cache(workspace)
    if not cache.get("outlines") and index_data is not None:
        cache = build_file_outlines_cache(workspace, index_data)
    outlines = cache.get("outlines") if isinstance(cache.get("outlines"), dict) else {}
    entries = index_data.get("entries") if isinstance(index_data, dict) and isinstance(index_data.get("entries"), dict) else {}

    result: list[dict[str, Any]] = []
    for rel_path in files:
        path = str(rel_path)
        item = outlines.get(path)
        if isinstance(item, dict):
            result.append(item)
            continue
        entry = entries.get(path)
        if isinstance(entry, dict):
            result.append(build_file_outline(workspace, path, entry))
    return result


def _outline_summary(
    path: str,
    entry: dict[str, Any],
    headings: list[str],
    exports: list[str],
) -> str:
    role = entry.get("role", "source")
    language = entry.get("language", "text")
    symbols = [
        str(symbol.get("name"))
        for symbol in entry.get("symbols", [])
        if isinstance(symbol, dict) and symbol.get("name")
    ]
    routes = entry.get("routes") if isinstance(entry.get("routes"), list) else []
    parts = [f"{path} is a {role} {language} file"]
    if symbols:
        parts.append("symbols: " + ", ".join(symbols[:8]))
    if headings:
        parts.append("headings: " + " > ".join(headings[:5]))
    if exports:
        parts.append("exports: " + ", ".join(exports[:8]))
    if routes:
        route_text = ", ".join(
            f"{route.get('method', '?')} {route.get('path', '/')}"
            for route in routes[:5]
            if isinstance(route, dict)
        )
        if route_text:
            parts.append("routes: " + route_text)
    return "; ".join(parts)[:500]


def _extract_headings(path: Path, language: str) -> list[str]:
    if language not in {"text", "markdown"} and path.suffix.lower() not in {".md", ".markdown", ".rst"}:
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    headings: list[str] = []
    for line in content.splitlines():
        markdown = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        rst = re.match(r"^\s*(.+?)\s*$", line)
        if markdown:
            headings.append(markdown.group(1).strip())
        elif rst and len(headings) < 20:
            text = rst.group(1).strip()
            if 3 <= len(text) <= 80 and not text.startswith(("-", "*", "`")):
                headings.append(text)
    return _unique(headings)[:20]


def _extract_exports(path: Path, language: str) -> list[str]:
    if language not in {"javascript", "typescript"}:
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    patterns = [
        r"export\s+(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"export\s+(?:default\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"export\s+(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"export\s*\{\s*([^}]+)\s*\}",
    ]
    exports: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, content):
            if "," in match:
                exports.extend(part.strip().split(" as ")[-1].strip() for part in match.split(","))
            else:
                exports.append(str(match).strip())
    return _unique(exports)[:20]


def _read_cache(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_cache()
    return data if isinstance(data, dict) else _empty_cache()


def _empty_cache() -> dict[str, Any]:
    return {"schema_version": 1, "outline_count": 0, "outlines": {}}


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result
