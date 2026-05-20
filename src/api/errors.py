"""Unified error model for nanoCursor API.

All API errors flow through ApiError so the frontend can rely on a consistent
`error.code` / `error.message` / `error.hint` / `error.request_id` shape.
"""

from __future__ import annotations

from enum import Enum


class ApiErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    RESOURCE_NOT_FOUND = "resource_not_found"
    WORKSPACE_NOT_OPEN = "workspace_not_open"
    WORKSPACE_PATH_INVALID = "workspace_path_invalid"
    RUN_CONFLICT = "run_conflict"
    RUN_NOT_ACTIVE = "run_not_active"
    APPROVAL_REQUIRED = "approval_required"
    ACTION_REQUIRES_CONFIRMATION = "action_requires_confirmation"
    CONFIG_MISSING = "config_missing"
    MCP_CONFIG_INVALID = "mcp_config_invalid"
    SKILL_INVALID = "skill_invalid"
    INTERNAL_ERROR = "internal_error"


class ApiError(Exception):
    """Unified API error with machine-readable code and human-readable hint."""

    def __init__(
        self,
        code: ApiErrorCode,
        message: str,
        status_code: int = 400,
        hint: str = "",
        details: dict | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.hint = hint
        self.details = details or {}
        super().__init__(message)


def http_status_to_code(status: int) -> str:
    """Map HTTP status to the public ApiErrorCode contract."""
    mapping = {
        400: ApiErrorCode.INVALID_REQUEST.value,
        401: ApiErrorCode.INVALID_REQUEST.value,
        403: ApiErrorCode.INVALID_REQUEST.value,
        404: ApiErrorCode.RESOURCE_NOT_FOUND.value,
        409: ApiErrorCode.RUN_CONFLICT.value,
        422: ApiErrorCode.INVALID_REQUEST.value,
        429: ApiErrorCode.RUN_CONFLICT.value,
        500: ApiErrorCode.INTERNAL_ERROR.value,
        502: ApiErrorCode.INTERNAL_ERROR.value,
        503: ApiErrorCode.INTERNAL_ERROR.value,
        504: ApiErrorCode.INTERNAL_ERROR.value,
    }
    return mapping.get(status, ApiErrorCode.INTERNAL_ERROR.value)
