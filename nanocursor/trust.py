"""Project configuration can start processes or change permissions."""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from nanocursor.runtime import app_home
from nanocursor.storage import read_json, state_lock, write_json


def fingerprint(workspace: Path, payload: list[dict]) -> str:
    # Preserve safe YAML values in unknown extension fields (dates, sets, etc.).
    data = yaml.safe_dump([str(workspace.resolve()), payload], sort_keys=True, allow_unicode=True)
    return hashlib.sha256(data.encode()).hexdigest()


def is_trusted(workspace: Path, digest: str) -> bool:
    records = read_json(app_home() / "trusted-projects.json")
    return records.get(str(workspace.resolve())) == digest


def grant(workspace: Path, digest: str) -> None:
    with state_lock(app_home()):
        path = app_home() / "trusted-projects.json"
        records = read_json(path)
        records[str(workspace.resolve())] = digest
        write_json(path, records)
