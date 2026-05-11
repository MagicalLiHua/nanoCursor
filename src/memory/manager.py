"""
MemoryManager - 跨会话持久化记忆系统（借鉴 s09_memory_system.py）

改进点：
- Markdown 文件存储，方便人类阅读和编辑
- 自动 consolidation 去重
- 分类记忆 (user/feedback/project/reference)
- 高 importance 记忆跨会话保留
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from src.infra.logger import logger

# Lazy-initialized memory directory path
_memory_dir_cache: Path | None = None

def _get_memory_dir() -> Path:
    global _memory_dir_cache
    if _memory_dir_cache is None:
        # Import here to avoid circular dependency
        from src.infra.config import WORKSPACE_DIR
        _memory_dir_cache = Path(WORKSPACE_DIR) / ".memory"
    return _memory_dir_cache

# Ensure memory directory exists on module load
_get_memory_dir().mkdir(parents=True, exist_ok=True)

# 记忆类型
MEMORY_CATEGORIES = ["user", "feedback", "project", "reference"]


@dataclass
class MemoryEntry:
    id: str
    category: str
    content: str
    importance: int = 1
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    access_count: int = 0
    session_id: str | None = None


class MemoryManager:
    """
    记忆管理器 - Markdown 文件持久化 + 自动合并

    目录结构：
    - .memory/user/     - 用户相关信息
    - .memory/feedback/ - 反馈记录
    - .memory/project/ - 项目信息
    - .memory/reference/- 参考资料
    """

    def __init__(self, memory_dir: Path = None):
        self.memory_dir = memory_dir or _get_memory_dir()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, MemoryEntry] = {}
        self._load_all()

    def _category_dir(self, category: str) -> Path:
        d = self.memory_dir / category
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_all(self):
        """加载所有记忆文件到缓存"""
        self._cache.clear()
        for category in MEMORY_CATEGORIES:
            cat_dir = self._category_dir(category)
            if not cat_dir.exists():
                continue
            for f in cat_dir.glob("*.md"):
                try:
                    entry = self._parse_memory_file(f, category)
                    if entry:
                        self._cache[entry.id] = entry
                except Exception:
                    pass

    def _parse_memory_file(self, path: Path, category: str) -> MemoryEntry | None:
        """解析 Markdown 记忆文件"""
        content = path.read_text(encoding="utf-8")

        # 提取 frontmatter (YAML 风格 --- ... ---)
        frontmatter = {}
        body = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        frontmatter[key.strip()] = value.strip()
                body = parts[2].strip()

        memory_id = frontmatter.get("id", path.stem)
        importance = int(frontmatter.get("importance", 1))
        tags = frontmatter.get("tags", "").split(",") if frontmatter.get("tags") else []
        created_at = float(frontmatter.get("created_at", time.time()))
        last_accessed_at = float(frontmatter.get("last_accessed_at", created_at))
        access_count = int(frontmatter.get("access_count", 0))
        session_id = frontmatter.get("session_id")

        return MemoryEntry(
            id=memory_id,
            category=category,
            content=body,
            importance=importance,
            tags=tags,
            created_at=created_at,
            last_accessed_at=last_accessed_at,
            access_count=access_count,
            session_id=session_id,
        )

    def _save_memory_file(self, entry: MemoryEntry):
        """保存记忆到 Markdown 文件"""
        cat_dir = self._category_dir(entry.category)
        file_path = cat_dir / f"{entry.id}.md"

        # 构造 frontmatter
        fm_lines = [
            "---",
            f"id: {entry.id}",
            f"category: {entry.category}",
            f"importance: {entry.importance}",
            f"tags: {','.join(entry.tags)}",
            f"created_at: {entry.created_at}",
            f"last_accessed_at: {entry.last_accessed_at}",
            f"access_count: {entry.access_count}",
            f"session_id: {entry.session_id or ''}",
            "---",
        ]

        content = "\n".join(fm_lines) + "\n\n" + entry.content
        file_path.write_text(content, encoding="utf-8")

    def save(
        self,
        category: str,
        content: str,
        importance: int = 1,
        tags: list[str] | None = None,
        session_id: str | None = None,
        memory_id: str | None = None,
    ) -> dict:
        """保存新记忆"""
        if category not in MEMORY_CATEGORIES:
            category = "reference"

        import uuid
        entry = MemoryEntry(
            id=memory_id or str(uuid.uuid4()),
            category=category,
            content=content,
            importance=importance,
            tags=tags or [],
            created_at=time.time(),
            last_accessed_at=time.time(),
            access_count=0,
            session_id=session_id,
        )

        self._save_memory_file(entry)
        self._cache[entry.id] = entry

        logger.info(f"[MemoryManager] Saved {category} memory: {entry.id[:8]}")
        return {"id": entry.id, "category": entry.category, "importance": entry.importance}

    def get(
        self,
        category: str | None = None,
        min_importance: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        """获取记忆列表"""
        results = []
        for entry in self._cache.values():
            if category and entry.category != category:
                continue
            if entry.importance < min_importance:
                continue
            results.append(self._entry_to_dict(entry))

        results.sort(key=lambda x: (x["importance"], x["last_accessed_at"]), reverse=True)
        return results[:limit]

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """全文搜索记忆"""
        results = []
        query_lower = query.lower()
        for entry in self._cache.values():
            if query_lower in entry.content.lower():
                results.append(self._entry_to_dict(entry))

        results.sort(key=lambda x: x["importance"], reverse=True)
        return results[:limit]

    def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        if memory_id not in self._cache:
            return False

        entry = self._cache[memory_id]
        file_path = self._category_dir(entry.category) / f"{memory_id}.md"
        if file_path.exists():
            file_path.unlink()

        del self._cache[memory_id]
        return True

    def update(self, memory_id: str, content: str | None = None, importance: int | None = None) -> dict | None:
        """更新记忆内容或重要性"""
        if memory_id not in self._cache:
            return None

        entry = self._cache[memory_id]
        if content is not None:
            entry.content = content
        if importance is not None:
            entry.importance = importance

        entry.last_accessed_at = time.time()
        self._save_memory_file(entry)

        return self._entry_to_dict(entry)

    def inc_access(self, memory_id: str):
        """增加访问计数"""
        if memory_id in self._cache:
            entry = self._cache[memory_id]
            entry.access_count += 1
            entry.last_accessed_at = time.time()
            self._save_memory_file(entry)

    def prime(self, session_id: str) -> list[dict]:
        """
        为新会话加载相关记忆：
        - 同 session_id 的记忆
        - 高 importance (>=7) 的记忆
        """
        results = []
        for entry in self._cache.values():
            if entry.session_id == session_id or entry.importance >= 7:
                entry.access_count += 1
                self._save_memory_file(entry)
                results.append(self._entry_to_dict(entry))

        results.sort(key=lambda x: x["importance"], reverse=True)
        logger.info(f"[MemoryManager] Primed {len(results)} memories for session {session_id[:8]}")
        return results

    def format_memories(self, memories: list[dict], max_items: int = 10) -> str:
        """格式化记忆列表用于提示"""
        if not memories:
            return "(no memories)"

        lines = []
        for m in memories[:max_items]:
            cat = m.get("category", "unknown").upper()
            content = m.get("content", "")[:200]
            imp = m.get("importance", 0)
            lines.append(f"[{cat}@{imp}] {content}")

        if len(memories) > max_items:
            lines.append(f"... and {len(memories) - max_items} more")

        return "\n".join(lines) if lines else "(no memories)"

    def _entry_to_dict(self, entry: MemoryEntry) -> dict:
        return {
            "id": entry.id,
            "category": entry.category,
            "content": entry.content,
            "importance": entry.importance,
            "tags": entry.tags,
            "created_at": entry.created_at,
            "last_accessed_at": entry.last_accessed_at,
            "access_count": entry.access_count,
            "session_id": entry.session_id,
        }

    def consolidate(self) -> int:
        """
        自动合并记忆：
        - 删除低重要性且长时间未访问的重复记忆
        - 合并相似内容
        返回删除的记忆数量
        """
        deleted = 0
        to_remove = []

        # 找出可删除的记忆：importance < 3 且超过 30 天未访问
        cutoff = time.time() - 30 * 86400
        for entry in self._cache.values():
            if entry.importance < 3 and entry.last_accessed_at < cutoff:
                # 检查是否有相似的高重要性记忆
                has_duplicate = any(
                    e.id != entry.id
                    and e.category == entry.category
                    and e.importance > entry.importance
                    and self._similar(entry.content, e.content)
                    for e in self._cache.values()
                )
                if has_duplicate:
                    to_remove.append(entry.id)

        for memory_id in to_remove:
            if self.delete(memory_id):
                deleted += 1

        if deleted > 0:
            logger.info(f"[MemoryManager] Consolidated: removed {deleted} duplicate memories")

        return deleted

    def _similar(self, content1: str, content2: str) -> bool:
        """简单的相似性检测（基于关键词重叠）"""
        words1 = set(content1.lower().split()[:20])
        words2 = set(content2.lower().split()[:20])
        if not words1 or not words2:
            return False
        overlap = len(words1 & words2)
        return overlap >= 5 and overlap / max(len(words1), len(words2)) > 0.5


# 全局单例
_memory_mgr: MemoryManager | None = None


def get_memory_manager() -> MemoryManager:
    global _memory_mgr
    if _memory_mgr is None:
        _memory_mgr = MemoryManager()
    return _memory_mgr


__all__ = ["MemoryManager", "get_memory_manager", "MEMORY_CATEGORIES", "MemoryEntry"]