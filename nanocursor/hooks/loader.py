from __future__ import annotations

import re

from nanocursor.hooks.conditions import ConditionParseError, parse_condition
from nanocursor.hooks.events import LifecycleEvent
from nanocursor.hooks.models import Action, Hook

_VALID_EVENTS = {e.value for e in LifecycleEvent}
_VALID_ACTION_TYPES = {"command", "prompt", "http"}
_LEGACY_COMMAND_CONTEXT = re.compile(
    r"\$(?:\{(?:EVENT|TOOL_NAME|FILE_PATH|MESSAGE|ERROR)\}|"
    r"(?:EVENT|TOOL_NAME|FILE_PATH|MESSAGE|ERROR)(?![A-Za-z0-9_])|TOOL_ARGS\.)"
)

_REQUIRED_FIELDS: dict[str, list[str]] = {
    "command": ["command"],
    "prompt": ["message"],
    "http": ["url"],
}


class HookConfigError(Exception):
    pass


def _identify(entry: dict, index: int) -> str:
    hook_id = entry.get("id", "")
    return f"hook '{hook_id}'" if hook_id else f"hook #{index + 1}"


def load_hooks(raw_hooks: list[dict] | None, *, source: str = "config") -> list[Hook]:
    if not raw_hooks:
        return []

    hooks: list[Hook] = []
    for i, entry in enumerate(raw_hooks):
        if not isinstance(entry, dict):
            raise HookConfigError(f"{source}: hook #{i + 1}: must be a mapping")
        label = f"{source}: {_identify(entry, i)}"

        event = entry.get("event")
        if not event:
            raise HookConfigError(f"{label}: missing 'event' field")
        if event not in _VALID_EVENTS:
            raise HookConfigError(
                f"{label}: invalid event '{event}', "
                f"must be one of: {', '.join(sorted(_VALID_EVENTS))}"
            )

        raw_action = entry.get("action")
        if not isinstance(raw_action, dict):
            raise HookConfigError(f"{label}: missing or invalid 'action' field")

        action_type = raw_action.get("type")
        if action_type == "agent":
            raise HookConfigError(f"{label}: agent Hook is not supported")
        if action_type not in _VALID_ACTION_TYPES:
            raise HookConfigError(
                f"{label}: invalid action type '{action_type}', "
                f"must be one of: {', '.join(sorted(_VALID_ACTION_TYPES))}"
            )

        required = _REQUIRED_FIELDS[action_type]
        for field_name in required:
            if not raw_action.get(field_name):
                raise HookConfigError(
                    f"{label}: action type '{action_type}' requires "
                    f"'{field_name}' field"
                )

        input_mode = raw_action.get("input", "none")
        if input_mode not in ("none", "context-json"):
            raise HookConfigError(f"{label}: input must be 'none' or 'context-json'")
        if action_type == "command":
            command = raw_action["command"]
            if not isinstance(command, str):
                raise HookConfigError(f"{label}: command must be a string")
            if _LEGACY_COMMAND_CONTEXT.search(command):
                raise HookConfigError(
                    f"{label}: legacy command context interpolation is unsafe; "
                    "use quoted $NANOCURSOR_HOOK_EVENT, $NANOCURSOR_HOOK_TOOL_NAME, "
                    "$NANOCURSOR_HOOK_FILE_PATH, or input: context-json for structured data"
                )

        reject = bool(entry.get("reject", False))
        if reject and event != "pre_tool_use":
            raise HookConfigError(
                f"{label}: 'reject' can only be used with 'pre_tool_use' event"
            )

        async_exec = bool(entry.get("async", False))
        if async_exec and event == "pre_tool_use":
            raise HookConfigError(
                f"{label}: 'async' cannot be used with 'pre_tool_use' event"
            )

        condition = None
        raw_if = entry.get("if")
        if raw_if:
            try:
                condition = parse_condition(str(raw_if))
            except ConditionParseError as e:
                raise HookConfigError(f"{label}: condition error: {e}") from e

        hook_id = entry.get("id", f"{event}_{i}")

        timeout = raw_action.get("timeout", 30)
        if not isinstance(timeout, int) or timeout <= 0:
            raise HookConfigError(f"{label}: timeout must be a positive integer")

        action = Action(
            type=action_type,
            command=raw_action.get("command", ""),
            message=raw_action.get("message", ""),
            url=raw_action.get("url", ""),
            method=raw_action.get("method", "POST"),
            body=raw_action.get("body", ""),
            headers=raw_action.get("headers", {}),
            prompt=raw_action.get("prompt", ""),
            timeout=timeout,
            input=input_mode,
        )

        hooks.append(
            Hook(
                id=hook_id,
                event=event,
                action=action,
                condition=condition,
                reject=reject,
                once=bool(entry.get("once", False)),
                async_exec=async_exec,
            )
        )

    return hooks
