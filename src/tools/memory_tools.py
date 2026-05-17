"""
Memory tools - agents can store and retrieve persistent cross-session memories.
"""

from src.infra.logger import logger
from src.memory.manager import get_memory_manager


# ==========================================
# Tool Schemas
# ==========================================

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "add_memory",
            "description": "Store an important piece of information in the persistent memory. Use for facts, decisions, context that should be remembered across sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The memory content to store"},
                    "category": {
                        "type": "string",
                        "description": "Category: user | feedback | project | reference",
                    },
                    "importance": {"type": "integer", "description": "Importance 0-10. High (>7) memories are loaded on every new session.", "default": 1},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags for search", "default": []},
                },
                "required": ["content", "category"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memories",
            "description": "Retrieve relevant memories from the persistent memory store. Use to recall past decisions, context, or error patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query to find relevant memories"},
                    "category": {"type": "string", "description": "Optional category filter: user | feedback | project | reference"},
                    "limit": {"type": "integer", "description": "Max memories to return", "default": 10},
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "Update the content or importance of an existing memory entry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "The ID of the memory to update"},
                    "content": {"type": "string", "description": "New content (optional)"},
                    "importance": {"type": "integer", "description": "New importance 0-10 (optional)"},
                },
                "required": ["memory_id"]
            }
        }
    },
]


# ==========================================
# Tool Implementations
# ==========================================

def add_memory(
    content: str,
    category: str,
    importance: int = 1,
    tags: list[str] | None = None,
) -> str:
    """Store a new memory entry."""
    try:
        mm = get_memory_manager()
        result = mm.save(category=category, content=content, importance=importance, tags=tags)
        if result.get("id"):
            imp_note = " (will prime on new sessions)" if importance >= 7 else ""
            return f"Memory stored: [{result['id'][:8]}] {category}@{importance}{imp_note}"
        return f"Failed to store memory: {result.get('error')}"
    except Exception as e:
        logger.error(f"add_memory failed: {e}")
        return f"Failed to add memory: {e}"


def recall_memories(
    query: str,
    category: str | None = None,
    limit: int = 10,
) -> str:
    """Search and retrieve memories."""
    try:
        mm = get_memory_manager()

        if query:
            memories = mm.search(query, limit)
        else:
            memories = mm.get(category=category, limit=limit)

        if not memories:
            return "No memories found."

        lines = [f"=== Memory Recall ({len(memories)} items) ==="]
        for m in memories:
            cat = m.get("category", "?").upper()
            imp = m.get("importance", 0)
            acc = m.get("access_count", 0)
            content = m.get("content", "")[:150]
            tags = m.get("tags", [])
            tag_str = f" [{', '.join(tags[:3])}]" if tags else ""
            lines.append(f"\n[{cat}@{imp}|×{acc}]{tag_str}")
            lines.append(f"  {content}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"recall_memories failed: {e}")
        return f"Failed to recall memories: {e}"


def update_memory(
    memory_id: str,
    content: str | None = None,
    importance: int | None = None,
) -> str:
    """Update an existing memory."""
    try:
        mm = get_memory_manager()
        result = mm.update(memory_id, content, importance)
        if result is None:
            return f"Memory not found: {memory_id[:8]}"
        return f"Memory updated: [{memory_id[:8]}] {result['category']}@{result['importance']}"
    except Exception as e:
        logger.error(f"update_memory failed: {e}")
        return f"Failed to update memory: {e}"