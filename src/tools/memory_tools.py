"""
Memory tools - agents can store and retrieve persistent cross-session memories.
"""

from src.infra import config as config_module
from src.infra.logger import logger


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
    workspace_dir: str | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> str:
    """Store a user-approved governed memory entry."""
    try:
        from src.api.services.memory_governance_service import create_memory_record

        category_map = {
            "user": ("global", "user_preference"),
            "feedback": ("workspace", "failure_pattern"),
            "project": ("workspace", "workflow_note"),
            "reference": ("workspace", "project_fact"),
        }
        scope, kind = category_map.get(category, ("workspace", "workflow_note"))
        result = create_memory_record(
            workspace_dir or config_module.WORKSPACE_DIR,
            scope=scope,
            kind=kind,
            content=content,
            source="user",
            importance=importance,
            tags=tags or [],
            conversation_id=conversation_id,
            run_id=run_id,
            source_ref=f"tool:add_memory:{run_id}" if run_id else "tool:add_memory",
            evidence_refs=[f"run:{run_id}"] if run_id else [],
            automatic=False,
        )
        if result.get("id"):
            return f"Memory stored: [{result['id'][:12]}] {scope}/{kind}@{importance}"
        return f"Failed to store memory: {result.get('error')}"
    except Exception as e:
        logger.error(f"add_memory failed: {e}")
        return f"Failed to add memory: {e}"


def recall_memories(
    query: str,
    category: str | None = None,
    limit: int = 10,
    workspace_dir: str | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> str:
    """Retrieve memories through the governed selector."""
    try:
        from src.api.services.memory_selection_service import select_memories

        result = select_memories(
            workspace_dir or config_module.WORKSPACE_DIR,
            prompt=query,
            conversation_id=conversation_id,
            run_id=run_id,
            budget_tokens=max(200, min(limit, 20) * 180),
        )
        memories = result.get("selected", [])[:limit]
        if not memories:
            return "No memories found."

        lines = [f"=== Governed Memory Recall ({len(memories)} items) ==="]
        for m in memories:
            cat = f"{m.get('scope', '?')}/{m.get('kind', '?')}".upper()
            imp = m.get("importance", 0)
            score = m.get("score", 0)
            summary = m.get("summary", "")[:220]
            lines.append(f"\n[{cat}@{imp}|score={score}]")
            lines.append(f"  {summary}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"recall_memories failed: {e}")
        return f"Failed to recall memories: {e}"


def update_memory(
    memory_id: str,
    content: str | None = None,
    importance: int | None = None,
    workspace_dir: str | None = None,
) -> str:
    """Update an existing governed memory."""
    try:
        from src.api.services.memory_governance_service import update_memory_record

        result = update_memory_record(
            workspace_dir or config_module.WORKSPACE_DIR,
            memory_id,
            content=content,
            importance=importance,
        )
        if result is None:
            return f"Memory not found: {memory_id[:12]}"
        return f"Memory updated: [{memory_id[:12]}] {result['scope']}/{result['kind']}@{result['importance']}"
    except Exception as e:
        logger.error(f"update_memory failed: {e}")
        return f"Failed to update memory: {e}"
