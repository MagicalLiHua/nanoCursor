"""Persistent recovery identities and fail-closed errors."""
from __future__ import annotations

from dataclasses import dataclass


class RecoveryError(RuntimeError):
    """A recovery invariant prevents execution."""


class RecoveryStorageError(RecoveryError):
    """Durability cannot be confirmed. Do not dispatch further effects."""


class RecoveryRequired(RecoveryError):
    def __init__(self, operations=None, message: str | None = None):
        self.operations = operations or []
        super().__init__(message or "Unresolved execution outcomes require /recover before continuing.")


class WorkspaceBusy(RecoveryError):
    """Another participant owns this checkout or conversation."""


class RecoveryIntegrityError(RecoveryStorageError):
    """Stored identities or evidence disagree; read-only diagnosis is required."""


@dataclass(frozen=True)
class WorkspaceIdentity:
    project_id: str
    workspace_id: str
    root: str
    git_common_dir: str | None = None
