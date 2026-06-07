"""Memory Profile service for nanoCursor user preferences."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.api.services.memory_governance_service import (
    create_memory_record,
    list_memory_records,
)
from src.infra import config as config_module


PREFERENCE_BUCKETS = [
    {
        "id": "code_style",
        "label": "代码风格",
        "description": "命名、注释、类型、格式化和代码组织习惯。",
        "keywords": ["代码风格", "code style", "type hint", "typing", "注释", "lint", "format", "命名"],
    },
    {
        "id": "ui_style",
        "label": "UI 风格",
        "description": "界面审美、布局密度、颜色、交互和组件偏好。",
        "keywords": ["ui", "界面", "前端设计", "视觉", "颜色", "布局", "交互", "好看"],
    },
    {
        "id": "tech_stack",
        "label": "常用技术栈",
        "description": "常用语言、框架、数据库、运行环境和工程工具。",
        "keywords": ["技术栈", "stack", "react", "vue", "fastapi", "python", "typescript", "数据库"],
    },
    {
        "id": "testing",
        "label": "测试偏好",
        "description": "单元测试、端到端测试、验证策略和质量门禁习惯。",
        "keywords": ["测试", "test", "pytest", "playwright", "验证", "质量", "coverage"],
    },
    {
        "id": "file_organization",
        "label": "文件组织偏好",
        "description": "目录结构、模块边界、文档位置和命名约定。",
        "keywords": ["文件", "目录", "结构", "模块", "organization", "folder", "path"],
    },
]

BUCKET_BY_ID = {bucket["id"]: bucket for bucket in PREFERENCE_BUCKETS}


def _workspace(workspace_dir: str | None = None) -> Path:
    root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _preference_type_from_tags(tags: list[str]) -> str | None:
    for tag in tags:
        if not isinstance(tag, str):
            continue
        if tag.startswith("preference:"):
            candidate = tag.split(":", 1)[1]
            if candidate in BUCKET_BY_ID:
                return candidate
    return None


def _infer_preference_type(memory: dict[str, Any]) -> str | None:
    tags = memory.get("tags") if isinstance(memory.get("tags"), list) else []
    tagged = _preference_type_from_tags(tags)
    if tagged:
        return tagged

    haystack = " ".join([str(memory.get("content", "")), " ".join(str(tag) for tag in tags)]).lower()
    for bucket in PREFERENCE_BUCKETS:
        if any(keyword.lower() in haystack for keyword in bucket["keywords"]):
            return bucket["id"]
    return None


def _confidence(memories: list[dict[str, Any]]) -> str:
    if any(memory.get("importance", 0) >= 8 for memory in memories):
        return "high"
    if memories:
        return "medium"
    return "empty"


def _memory_item(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": memory.get("id", ""),
        "category": "user",
        "content": memory.get("content", ""),
        "importance": memory.get("importance", 0),
        "tags": memory.get("tags") if isinstance(memory.get("tags"), list) else [],
        "created_at": memory.get("created_at"),
        "last_accessed_at": memory.get("last_used_at") or memory.get("updated_at"),
    }


def build_memory_profile(workspace_dir: str | None = None, min_importance: int = 0) -> dict[str, Any]:
    """Build the user preference profile from existing memories."""
    workspace = _workspace(workspace_dir)
    memories = [
        memory
        for memory in list_memory_records(
            str(workspace),
            scope="global",
            status="active",
            limit=1000,
        )
        if memory.get("kind") == "user_preference"
        and int(memory.get("importance") or 0) >= min_importance
    ]
    grouped: dict[str, list[dict[str, Any]]] = {bucket["id"]: [] for bucket in PREFERENCE_BUCKETS}

    for memory in memories:
        bucket_id = _infer_preference_type(memory)
        if bucket_id:
            grouped[bucket_id].append(memory)

    buckets = []
    prompt_lines = []
    for bucket in PREFERENCE_BUCKETS:
        bucket_memories = sorted(
            grouped[bucket["id"]],
            key=lambda item: (
                item.get("importance", 0),
                item.get("last_used_at") or item.get("updated_at") or 0,
            ),
            reverse=True,
        )
        buckets.append(
            {
                "id": bucket["id"],
                "label": bucket["label"],
                "description": bucket["description"],
                "confidence": _confidence(bucket_memories),
                "memories": [_memory_item(memory) for memory in bucket_memories[:8]],
            }
        )
        high_items = [memory for memory in bucket_memories if memory.get("importance", 0) >= 7]
        if high_items:
            prompt_lines.append(f"{bucket['label']}:")
            prompt_lines.extend(f"- {memory.get('content', '')}" for memory in high_items[:3])

    preference_count = sum(len(items) for items in grouped.values())
    high_importance_count = sum(1 for memory in memories if memory.get("importance", 0) >= 7)
    prompt_context = "\n".join(prompt_lines)

    return {
        "workspace_dir": str(workspace),
        "total_memories": len(memories),
        "preference_count": preference_count,
        "high_importance_count": high_importance_count,
        "prompt_context": prompt_context,
        "buckets": buckets,
    }


def add_preference_memory(
    preference_type: str,
    content: str,
    importance: int = 8,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Store a user preference memory with structured tags."""
    normalized_type = preference_type.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized_type not in BUCKET_BY_ID:
        raise ValueError(f"Unknown preference type: {preference_type}")

    workspace = _workspace(workspace_dir)
    saved = create_memory_record(
        str(workspace),
        scope="global",
        kind="user_preference",
        content=content.strip(),
        source="user",
        importance=importance,
        tags=["preference", f"preference:{normalized_type}"],
        source_ref="preferences_api",
    )
    return {**saved, "category": "user"}
