"""One-time migration from legacy Markdown memory into governed memory."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from src.api.services.memory_governance_service import (
    create_memory_record,
    list_memory_records,
)


_CATEGORY_MAPPING = {
    "user": ("global", "user_preference"),
    "feedback": ("workspace", "failure_pattern"),
    "project": ("workspace", "project_fact"),
    "reference": ("workspace", "workflow_note"),
}


def migrate_legacy_memory(
    workspace_dir: str,
    *,
    dry_run: bool = False,
    archive: bool = False,
) -> dict[str, Any]:
    """Import `.memory/*.md` files exactly once into governed memory."""
    workspace = Path(workspace_dir).resolve()
    legacy_root = workspace / ".memory"
    existing_refs = {
        str(item.get("source_ref") or "")
        for item in list_memory_records(str(workspace), include_deleted=True, limit=1000)
    }
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    if not legacy_root.is_dir():
        return _result(workspace, legacy_root, dry_run, archive, imported, skipped, errors)

    for path in sorted(legacy_root.glob("*/*.md")):
        relative = str(path.relative_to(workspace))
        source_ref = f"legacy-memory:{relative}"
        if source_ref in existing_refs:
            skipped.append({"path": relative, "reason": "already_imported"})
            continue
        try:
            parsed = _parse_legacy_file(path)
            category = path.parent.name if path.parent.name in _CATEGORY_MAPPING else "reference"
            scope, kind = _CATEGORY_MAPPING[category]
            if dry_run:
                imported.append(
                    {
                        "path": relative,
                        "scope": scope,
                        "kind": kind,
                        "content": parsed["content"],
                        "importance": parsed["importance"],
                    }
                )
                continue
            record = create_memory_record(
                str(workspace),
                scope=scope,
                kind=kind,
                content=parsed["content"],
                source="legacy",
                tags=parsed["tags"],
                source_ref=source_ref,
                confidence=0.6,
                importance=parsed["importance"],
                automatic=False,
            )
            imported.append({"path": relative, "memory_id": record["id"], "scope": scope, "kind": kind})
            existing_refs.add(source_ref)
        except (OSError, TypeError, ValueError) as exc:
            errors.append({"path": relative, "error": str(exc)})

    archived_to = None
    if archive and not dry_run and not errors and legacy_root.exists():
        archived_path = workspace / f".memory.migrated-{int(time.time())}"
        legacy_root.rename(archived_path)
        archived_to = str(archived_path)

    result = _result(workspace, legacy_root, dry_run, archive, imported, skipped, errors)
    result["archived_to"] = archived_to
    return result


def _parse_legacy_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    metadata: dict[str, str] = {}
    body = text.strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
            body = parts[2].strip()
    if not body:
        raise ValueError("legacy memory content is empty")
    tags = [tag.strip() for tag in metadata.get("tags", "").split(",") if tag.strip()]
    try:
        importance = max(0, min(int(metadata.get("importance", "1")), 10))
    except ValueError:
        importance = 1
    return {"content": body, "tags": tags, "importance": importance}


def _result(
    workspace: Path,
    legacy_root: Path,
    dry_run: bool,
    archive: bool,
    imported: list[dict[str, Any]],
    skipped: list[dict[str, str]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "workspace_dir": str(workspace),
        "legacy_root": str(legacy_root),
        "legacy_exists": legacy_root.is_dir(),
        "dry_run": dry_run,
        "archive_requested": archive,
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "archived_to": None,
    }
