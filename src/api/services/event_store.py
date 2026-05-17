"""Persistent run event storage for the AgentHub web experience."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from src.api.models import AgentEvent
from src.infra import config as config_module


class EventStore:
    """Stores run sessions and append-only events under the active workspace."""

    def __init__(self):
        self._lock = threading.RLock()

    def _root(self, workspace_dir: str | None = None) -> Path:
        root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
        run_root = root / ".nanocursor" / "runs"
        run_root.mkdir(parents=True, exist_ok=True)
        return run_root

    def run_dir(self, thread_id: str, workspace_dir: str | None = None) -> Path:
        safe_id = thread_id.replace("/", "_").replace("\\", "_")
        path = self._root(workspace_dir) / safe_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_path(self, thread_id: str, workspace_dir: str | None = None) -> Path:
        return self.run_dir(thread_id, workspace_dir) / "session.json"

    def events_path(self, thread_id: str, workspace_dir: str | None = None) -> Path:
        return self.run_dir(thread_id, workspace_dir) / "events.jsonl"

    def create_session(
        self,
        thread_id: str,
        prompt: str,
        workspace_dir: str,
        status: str = "running",
        mode: str = "agenthub_delivery",
    ) -> dict[str, Any]:
        now = time.time()
        session = {
            "thread_id": thread_id,
            "workspace_dir": str(Path(workspace_dir).resolve()),
            "status": status,
            "prompt": prompt,
            "mode": mode,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self.session_path(thread_id, workspace_dir).write_text(
                json.dumps(session, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return session

    def get_session(
        self, thread_id: str, workspace_dir: str | None = None
    ) -> dict[str, Any] | None:
        path = self.session_path(thread_id, workspace_dir)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def update_session(
        self, thread_id: str, workspace_dir: str | None = None, **changes: Any
    ) -> dict[str, Any] | None:
        with self._lock:
            session = self.get_session(thread_id, workspace_dir)
            if not session:
                return None
            session.update(changes)
            session["updated_at"] = time.time()
            self.session_path(thread_id, workspace_dir).write_text(
                json.dumps(session, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return session

    def append_event(
        self,
        thread_id: str,
        event_type: str,
        title: str = "",
        content: str = "",
        agent: str = "lead",
        payload: dict[str, Any] | None = None,
        workspace_dir: str | None = None,
    ) -> AgentEvent:
        event = AgentEvent(
            id=str(uuid.uuid4()),
            thread_id=thread_id,
            type=event_type,
            timestamp=time.time(),
            agent=agent,
            title=title,
            content=content,
            payload=payload or {},
        )
        with self._lock:
            with self.events_path(thread_id, workspace_dir).open("a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        return event

    def list_events(
        self, thread_id: str, workspace_dir: str | None = None, after: int = 0
    ) -> list[AgentEvent]:
        path = self.events_path(thread_id, workspace_dir)
        if not path.exists():
            return []

        events: list[AgentEvent] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        for line in lines[max(after, 0):]:
            if not line.strip():
                continue
            try:
                events.append(AgentEvent(**json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return events

    def count_events(self, thread_id: str, workspace_dir: str | None = None) -> int:
        path = self.events_path(thread_id, workspace_dir)
        if not path.exists():
            return 0
        try:
            return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            return 0


event_store = EventStore()


def get_event_store() -> EventStore:
    return event_store
