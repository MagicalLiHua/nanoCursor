from __future__ import annotations

from src.api.services.legacy_memory_migration_service import migrate_legacy_memory
from src.api.services.memory_governance_service import list_memory_records


def _write_legacy_memory(workspace, category: str, name: str, content: str, importance: int = 7):
    target = workspace / ".memory" / category / f"{name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            [
                "---",
                f"id: {name}",
                f"category: {category}",
                f"importance: {importance}",
                "tags: legacy,test",
                "---",
                "",
                content,
            ]
        ),
        encoding="utf-8",
    )
    return target


def test_migrate_legacy_memory_is_idempotent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_legacy_memory(workspace, "user", "ui-pref", "Prefer a restrained UI.", 8)
    _write_legacy_memory(workspace, "project", "project-fact", "Backend uses FastAPI.", 6)

    first = migrate_legacy_memory(str(workspace))
    second = migrate_legacy_memory(str(workspace))
    memories = list_memory_records(str(workspace), include_deleted=True, limit=1000)

    assert first["imported_count"] == 2
    assert first["error_count"] == 0
    assert second["imported_count"] == 0
    assert second["skipped_count"] == 2
    assert {(item["scope"], item["kind"]) for item in memories} == {
        ("global", "user_preference"),
        ("workspace", "project_fact"),
    }


def test_migrate_legacy_memory_dry_run_does_not_write(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_legacy_memory(workspace, "feedback", "failure", "Run tests before delivery.")

    result = migrate_legacy_memory(str(workspace), dry_run=True)

    assert result["imported_count"] == 1
    assert list_memory_records(str(workspace), include_deleted=True) == []


def test_migrate_legacy_memory_can_archive_source_after_success(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_legacy_memory(workspace, "reference", "note", "Use the current API contract.")

    result = migrate_legacy_memory(str(workspace), archive=True)

    assert result["error_count"] == 0
    assert result["archived_to"]
    assert not (workspace / ".memory").exists()
