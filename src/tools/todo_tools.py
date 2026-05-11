"""
Todo tools - agents can interact with the user-facing todo list via these tools.
Supports add, list, complete, and remove operations.
"""

import json
from typing import Any

from src.infra.logger import logger
from src.infra import db as nano_db


# ==========================================
# Tool Schemas
# ==========================================

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": "Add a new todo item to the list. Use when you need to track a task that needs to be done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title/description of the todo item"},
                    "priority": {"type": "integer", "description": "Priority level (higher = more important, default 0)"},
                    "category": {"type": "string", "description": "Optional category for grouping todos (e.g., 'bug', 'feature', 'refactor')"},
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_todos",
            "description": "List all todo items, grouped by status. Use to see what needs to be done.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "complete_todo",
            "description": "Mark a todo item as completed. Use when a task has been finished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_id": {"type": "string", "description": "The ID of the todo to mark as completed"},
                },
                "required": ["todo_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_todo",
            "description": "Delete a todo item permanently.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_id": {"type": "string", "description": "The ID of the todo to delete"},
                },
                "required": ["todo_id"]
            }
        }
    },
]


# ==========================================
# Tool Implementations
# ==========================================

async def add_todo(workspace: str, title: str, priority: int = 0, category: str | None = None) -> str:
    """Add a new todo item."""
    try:
        item = nano_db.todo_create(title=title, priority=priority, category=category)
        return f"Todo added: [{item['id']}] {title} (priority={priority}, category={category})"
    except Exception as e:
        logger.error(f"add_todo failed: {e}")
        return f"Failed to add todo: {e}"


async def list_todos(workspace: str) -> str:
    """List all todo items grouped by status."""
    try:
        todos = nano_db.todo_get_all()
        if not todos:
            return "No todo items yet."

        pending = [t for t in todos if t["status"] == "pending"]
        completed = [t for t in todos if t["status"] == "completed"]

        lines = ["=== Todo List ==="]
        if pending:
            lines.append(f"\n[Pending] ({len(pending)})")
            for t in pending:
                cat = f" [{t['category']}]" if t["category"] else ""
                pri = f" ⚡{t['priority']}" if t["priority"] > 0 else ""
                lines.append(f"  - [{t['id'][:8]}] {t['title']}{cat}{pri}")
        if completed:
            lines.append(f"\n[Completed] ({len(completed)})")
            for t in completed:
                lines.append(f"  ✓ [{t['id'][:8]}] {t['title']}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"list_todos failed: {e}")
        return f"Failed to list todos: {e}"


async def complete_todo(workspace: str, todo_id: str) -> str:
    """Mark a todo as completed."""
    try:
        result = nano_db.todo_complete(todo_id)
        if result is None:
            return f"Todo not found: {todo_id}"
        return f"Completed: [{todo_id[:8]}] {result['title']}"
    except Exception as e:
        logger.error(f"complete_todo failed: {e}")
        return f"Failed to complete todo: {e}"


async def remove_todo(workspace: str, todo_id: str) -> str:
    """Delete a todo item."""
    try:
        deleted = nano_db.todo_delete(todo_id)
        if not deleted:
            return f"Todo not found: {todo_id}"
        return f"Deleted todo: {todo_id[:8]}"
    except Exception as e:
        logger.error(f"remove_todo failed: {e}")
        return f"Failed to remove todo: {e}"