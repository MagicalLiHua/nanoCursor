from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

from nanocursor.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from nanocursor.worktree.manager import WorktreeManager


class ExitWorktreeParams(BaseModel):
    action: str = Field(
        default="keep",
        description='"keep" preserves files and branch. Destructive removal requires a user /worktree command.',
    )
    discard_changes: Optional[bool] = Field(
        default=None,
        description=(
            "Legacy compatibility only; this flag cannot authorize deletion."
        ),
    )


class ExitWorktreeTool(Tool):
    name = "ExitWorktree"
    description = (
        "Exits a worktree session created by EnterWorktree and restores "
        "the original working directory"
    )
    params_model = ExitWorktreeParams
    category = "command"
    should_defer = True


    def __init__(self, worktree_manager: WorktreeManager) -> None:
        self._manager = worktree_manager


    async def execute(self, params: ExitWorktreeParams) -> ToolResult:
        session = self._manager.get_current_session()
        if session is None:
            return ToolResult(
                output=(
                    "No-op: there is no active EnterWorktree session to exit. "
                    "This tool only operates on worktrees created by EnterWorktree "
                    "in the current session — it will not touch worktrees created "
                        "manually. No filesystem changes were made."
                ),
                is_error=True,
            )

        action = params.action
        if action not in ("keep", "remove"):
            return ToolResult(
                output=f'Invalid action "{action}". Must be "keep" or "remove".',
                is_error=True,
            )

        if action == "remove":
            return ToolResult(
                output="Removal requires the user to run /worktree exit --remove and confirm the displayed checkout. "
                       "Model-supplied discard_changes cannot authorize deletion. Use action keep to preserve this work.",
                is_error=True,
            )

        worktree_path = session.worktree_path
        original_cwd = session.original_cwd
        wt_name = session.worktree_name

        try:
            await self._manager.exit(wt_name, action=action)
        except Exception as e:
            return ToolResult(
                output=f"Error exiting worktree: {e}", is_error=True
            )

        if action == "keep":
            return ToolResult(
                output=(
                    f"Exited worktree. Your work is preserved at {worktree_path}. "
                    f"Session is now back in {original_cwd}."
                )
            )

        return ToolResult(
            output=(
                f"Exited and removed worktree at {worktree_path}. "
                f"Session is now back in {original_cwd}."
            )
        )
