"""Explicit compatibility and retirement boundaries for the backend product API."""

from __future__ import annotations


# These aliases remain because the active frontend or documented public startup
# contract still uses them. New API work should use the canonical `/api/runs/*`
# surface instead.
RETAINED_COMPATIBILITY_ROUTES = frozenset(
    {
        ("POST", "/api/run"),
        ("GET", "/api/run/{thread_id}/events"),
    }
)


# Retired routes must not be registered again. Their replacements are the
# run-scoped task board, ephemeral-agent runtime, and canonical run endpoints.
RETIRED_API_ROUTES = frozenset(
    {
        ("POST", "/api/run/{thread_id}/cancel"),
        ("GET", "/api/todos"),
        ("POST", "/api/todos"),
        ("PATCH", "/api/todos/{todo_id}/complete"),
        ("DELETE", "/api/todos/{todo_id}"),
        ("GET", "/api/subagents"),
        ("GET", "/api/subagents/{subagent_id}"),
    }
)


# These model-facing tools were replaced by the shared task board and
# run-scoped `spawn_agent`/`gather_agents` runtime.
RETIRED_MODEL_TOOLS = frozenset(
    {
        "TodoWrite",
        "TodoList",
        "task",
        "spawn_teammate",
        "list_teammates",
        "send_message",
        "read_inbox",
        "broadcast",
        "shutdown_request",
        "shutdown_response",
        "plan_approval",
        "claim_task",
    }
)


LEGACY_MODULE_REPLACEMENTS = {
    "src.team.team": "src.agent.agent_pool + src.api.services.ephemeral_agent_service",
}


LEGACY_FILE_TOOL_MODULE = "src.tools.file_tools"
CANONICAL_FILE_TOOL_MODULE = "src.tools.file_ops"


RETIRED_PRODUCT_IMPORTS = frozenset(LEGACY_MODULE_REPLACEMENTS)


RETIRED_IMPLEMENTATION_PATHS = frozenset(
    {
        "src/infra/db.py",
        "src/memory/manager.py",
        "src/tools/todo_tools.py",
    }
)


# Only these modules may call the remaining legacy workflow compatibility
# adapter. State reads, event writes, demo runs, and benchmark runs must not use
# it, so this list should shrink as the main Agent Loop is migrated.
ALLOWED_LEGACY_WORKFLOW_ADAPTER_CONSUMERS = frozenset(
    {
        "src/api/services/workflow_thread_service.py",
    }
)
