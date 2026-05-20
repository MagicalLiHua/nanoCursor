"""
Database management for nanoCursor.
SQLite for cross-session persistence (todos, memories).
Session state stays in Redis/CheckpointManager.
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Any

from src.infra.logger import logger

# DB path — project root by default, overridable for tests and isolated tooling.
_DB_PATH = os.path.abspath(os.path.expanduser(
    os.getenv(
        "NANOCURSOR_DB_PATH",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "nanocursor.db"),
    )
))


def get_db_path() -> str:
    return _DB_PATH


@contextmanager
def get_connection():
    """Get a SQLite connection, yield it, commit on success, rollback on error."""
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize database schema (create tables if not exist)."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Todos table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                priority INTEGER NOT NULL DEFAULT 0,
                category TEXT,
                metadata TEXT DEFAULT '{}',
                created_at REAL NOT NULL,
                completed_at REAL
            )
        """)

        # Memories table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                importance INTEGER NOT NULL DEFAULT 1,
                tags TEXT DEFAULT '[]',
                created_at REAL NOT NULL,
                last_accessed_at REAL NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                session_id TEXT
            )
        """)

        # Sub-agents table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subagents (
                id TEXT PRIMARY KEY,
                parent_task_id TEXT NOT NULL,
                instruction TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                spawned_at REAL NOT NULL,
                completed_at REAL,
                assigned_agent TEXT NOT NULL,
                thread_id TEXT
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subagents_status ON subagents(status)")

        logger.info(f"Database initialized at {_DB_PATH}")


# ==========================================
# Todo CRUD
# ==========================================

def todo_create(title: str, priority: int = 0, category: str | None = None, metadata: dict | None = None) -> dict:
    """Create a new todo item."""
    import uuid, time, json
    id_ = str(uuid.uuid4())
    now = time.time()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO todos (id, title, status, priority, category, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id_, title, "pending", priority, category, json.dumps(metadata or {}), now)
        )
    return {"id": id_, "title": title, "status": "pending", "priority": priority, "category": category, "metadata": metadata or {}, "created_at": now, "completed_at": None}


def todo_get_all() -> list[dict]:
    """Get all todos ordered by created_at."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM todos ORDER BY created_at DESC")
        rows = cursor.fetchall()
    import json
    return [_row_to_todo(r, json) for r in rows]


def todo_complete(todo_id: str) -> dict | None:
    """Mark a todo as completed."""
    import time
    now = time.time()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE todos SET status = 'completed', completed_at = ? WHERE id = ?", (now, todo_id))
        if cursor.rowcount == 0:
            return None
        cursor.execute("SELECT * FROM todos WHERE id = ?", (todo_id,))
        row = cursor.fetchone()
    import json
    return _row_to_todo(row, json) if row else None


def todo_delete(todo_id: str) -> bool:
    """Delete a todo."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        return cursor.rowcount > 0


def _row_to_todo(row, json) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "category": row["category"],
        "metadata": json.loads(row["metadata"] or "{}"),
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


# ==========================================
# Memory CRUD
# ==========================================

def memory_save(category: str, content: str, importance: int = 1, tags: list[str] | None = None, session_id: str | None = None) -> dict:
    """Save a new memory entry."""
    import uuid, time, json
    id_ = str(uuid.uuid4())
    now = time.time()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO memories (id, category, content, importance, tags, created_at, last_accessed_at, access_count, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id_, category, content, importance, json.dumps(tags or []), now, now, 0, session_id)
        )
    return {"id": id_, "category": category, "content": content, "importance": importance, "tags": tags or [], "created_at": now, "last_accessed_at": now, "access_count": 0, "session_id": session_id}


def memory_get_all(category: str | None = None, min_importance: int = 0, limit: int = 50) -> list[dict]:
    """Get memories, optionally filtered by category and importance."""
    import json
    with get_connection() as conn:
        cursor = conn.cursor()
        if category:
            cursor.execute("SELECT * FROM memories WHERE category = ? AND importance >= ? ORDER BY importance DESC, created_at DESC LIMIT ?", (category, min_importance, limit))
        else:
            cursor.execute("SELECT * FROM memories WHERE importance >= ? ORDER BY importance DESC, created_at DESC LIMIT ?", (min_importance, limit))
        rows = cursor.fetchall()
    return [_row_to_memory(r, json) for r in rows]


def memory_search(query: str, limit: int = 20) -> list[dict]:
    """Full-text search on memory content."""
    import json
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM memories WHERE content LIKE ? ORDER BY importance DESC LIMIT ?", (f"%{query}%", limit))
        rows = cursor.fetchall()
    return [_row_to_memory(r, json) for r in rows]


def memory_delete(memory_id: str) -> bool:
    """Delete a memory."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cursor.rowcount > 0


def memory_update(memory_id: str, content: str | None = None, importance: int | None = None) -> dict | None:
    """Update memory content and/or importance."""
    import json
    with get_connection() as conn:
        cursor = conn.cursor()
        if content is not None and importance is not None:
            cursor.execute("UPDATE memories SET content = ?, importance = ? WHERE id = ?", (content, importance, memory_id))
        elif content is not None:
            cursor.execute("UPDATE memories SET content = ? WHERE id = ?", (content, memory_id))
        elif importance is not None:
            cursor.execute("UPDATE memories SET importance = ? WHERE id = ?", (importance, memory_id))
        if cursor.rowcount == 0:
            return None
        cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
        row = cursor.fetchone()
    return _row_to_memory(row, json) if row else None


def memory_prime(session_id: str) -> list[dict]:
    """Load memories for a new session: same session_id OR high importance."""
    import json
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM memories WHERE session_id = ? OR importance >= 7 ORDER BY last_accessed_at DESC LIMIT 50",
            (session_id,)
        )
        rows = cursor.fetchall()
    return [_row_to_memory(r, json) for r in rows]


def memory_inc_access(memory_id: str):
    """Increment access_count and update last_accessed_at."""
    import time
    now = time.time()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?", (now, memory_id))


def _row_to_memory(row, json) -> dict:
    return {
        "id": row["id"],
        "category": row["category"],
        "content": row["content"],
        "importance": row["importance"],
        "tags": json.loads(row["tags"] or "[]"),
        "created_at": row["created_at"],
        "last_accessed_at": row["last_accessed_at"],
        "access_count": row["access_count"],
        "session_id": row["session_id"],
    }


# ==========================================
# SubAgent CRUD
# ==========================================

def subagent_create(parent_task_id: str, instruction: str, assigned_agent: str, thread_id: str | None = None) -> dict:
    """Create a new subagent record."""
    import uuid, time
    id_ = str(uuid.uuid4())
    now = time.time()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO subagents (id, parent_task_id, instruction, status, spawned_at, assigned_agent, thread_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id_, parent_task_id, instruction, "pending", now, assigned_agent, thread_id)
        )
    return {"id": id_, "parent_task_id": parent_task_id, "instruction": instruction, "status": "pending", "result": None, "spawned_at": now, "completed_at": None, "assigned_agent": assigned_agent}


def subagent_update_status(subagent_id: str, status: str, result: str | None = None):
    """Update subagent status and optionally result."""
    import time
    now = time.time()
    with get_connection() as conn:
        cursor = conn.cursor()
        if result is not None:
            cursor.execute("UPDATE subagents SET status = ?, result = ?, completed_at = ? WHERE id = ?", (status, result, now, subagent_id))
        else:
            cursor.execute("UPDATE subagents SET status = ? WHERE id = ?", (status, subagent_id))


def subagent_get(subagent_id: str) -> dict | None:
    """Get subagent by id."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM subagents WHERE id = ?", (subagent_id,))
        row = cursor.fetchone()
    return dict(row) if row else None


def subagent_get_active() -> list[dict]:
    """Get all running subagents."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM subagents WHERE status = 'running'")
        rows = cursor.fetchall()
    return [dict(r) for r in rows]
