"""Atomic edits of one user MCP entry, preserving unrelated settings."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from nanocursor.config import ConfigError, _validate_layer
from nanocursor.runtime import app_home
from nanocursor.storage import atomic_write, read_private_file, state_lock


class MCPSettings:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or app_home()
        self.path = self.directory / "config.yaml"

    def read(self) -> tuple[dict, bytes]:
        snapshot = read_private_file(self.path)
        try:
            raw = yaml.safe_load(snapshot) if snapshot else {}
        except (yaml.YAMLError, UnicodeError) as exc:
            raise ConfigError("Invalid user config.yaml; repair it before managing MCP") from exc
        if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
            raise ConfigError("User config.yaml must be a mapping")
        if type(raw.get("schema_version", 1)) is not int or raw.get("schema_version", 1) not in (1, 2):
            raise ConfigError("Unsupported user configuration schema")
        _validate_layer(raw)
        entries = raw.get("mcp_servers") or []
        names = [entry["name"] for entry in entries]
        if len(names) != len(set(names)):
            raise ConfigError("Duplicate MCP server names in user configuration")
        return raw, snapshot

    def save(self, entry: dict, *, enabled: bool, expected: bytes) -> bytes:
        with state_lock(self.directory):
            raw, snapshot = self.read()
            if snapshot != expected:
                raise ConfigError("User configuration changed; retry MCP management without overwriting it")
            updated = deepcopy(raw)
            updated.setdefault("schema_version", 2 if not snapshot else 1)
            replacement = {**deepcopy(entry), "enabled": enabled}
            entries = updated.get("mcp_servers") or []
            updated["mcp_servers"] = entries
            for index, previous in enumerate(entries):
                if previous["name"] == entry["name"]:
                    entries[index] = replacement
                    break
            else:
                entries.append(replacement)
            _validate_layer(updated)
            encoded = yaml.safe_dump(updated, allow_unicode=True, sort_keys=False).encode()
            atomic_write(self.path, encoded)
            return encoded


def check_project_override(workspace: Path, name: str, *, starting: bool) -> tuple[bytes, bytes]:
    snapshots = []
    for filename in ("config.yaml", "config.local.yaml"):
        path = workspace / ".nanocursor" / filename
        snapshot = read_private_file(path)
        snapshots.append(snapshot)
        try:
            raw = yaml.safe_load(snapshot) if snapshot else {}
        except (yaml.YAMLError, UnicodeError) as exc:
            raise ConfigError("Project configuration is invalid; repair it before managing MCP") from exc
        if not isinstance(raw, dict):
            raise ConfigError("Project configuration must be a mapping")
        entries = raw.get("mcp_servers")
        if entries is not None and not isinstance(entries, list):
            raise ConfigError("Project mcp_servers must be a list")
        if (starting and entries == []) or any(isinstance(entry, dict) and entry.get("name") == name
                                              for entry in entries or []):
            raise ConfigError(f"Project configuration overrides MCP '{name}'; edit {path} first. "
                              "ManageMCP changes user configuration only.")
    return tuple(snapshots)
