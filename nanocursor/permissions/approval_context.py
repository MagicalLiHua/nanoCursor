"""User-origin authorization and immutable, single-call review snapshots."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

MAX_INPUT_BYTES = 32_000


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class AuthorizationContext:
    # Only the interactive input adapter writes these records. Conversation
    # role=user also contains system/agent messages and is never a source here.
    records: list[dict[str, str]] = field(default_factory=list)
    complete: bool = True
    version: int = 0

    def add(self, text: str, *, source: str = "direct_user") -> None:
        self.version += 1
        record = {"source": source, "text": text}
        if len(canonical([*self.records, record]).encode("utf-8")) > MAX_INPUT_BYTES:
            self.complete = False
            return  # Never approve using a silently truncated set of constraints.
        self.records.append(record)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> AuthorizationContext:
        if not isinstance(raw, dict) or set(raw) != {"records", "complete", "version"}:
            return cls(complete=False)
        records = raw["records"]
        if (type(raw["complete"]) is not bool or type(raw["version"]) is not int
                or raw["version"] < 0 or not isinstance(records, list)
                or any(not isinstance(r, dict) or set(r) != {"source", "text"}
                       or r["source"] not in {"direct_user", "human_denial"}
                       or not isinstance(r["text"], str) for r in records)
                or len(canonical(raw).encode("utf-8")) > MAX_INPUT_BYTES + 1000):
            return cls(complete=False)
        return cls(records=[dict(r) for r in records], complete=raw["complete"], version=raw["version"])


@dataclass(frozen=True)
class ReviewRequest:
    payload: str
    binding: str
    context_complete: bool


def build_request(agent: Any, tool: Any, arguments: dict, source: str) -> ReviewRequest:
    """Snapshot all application state that can invalidate a pending decision."""
    controller = agent.approval_controller
    checker = agent.permission_checker
    authorization = controller.authorization
    rules = [[asdict(r) for r in tier] for tier in checker.rule_engine._load_tiers()]
    execution = tool.execution_details(agent.work_dir, agent.sandbox_root)
    restrictions = {
        "permission_mode": checker.mode.value,
        "rules": rules,
        "session_allows": sorted(checker._session_allowed),
        "sandbox_auto_allow": checker.sandbox_enabled,
        "dangerous_patterns": [(p.pattern, p.flags) for p, _ in checker.detector._patterns],
    }
    payload = canonical({
        "command": arguments["command"], "arguments": arguments,
        "cwd": agent.work_dir, "workspace": agent.sandbox_root or agent.work_dir,
        "is_worktree": bool(agent.sandbox_root), "execution": execution,
        "restrictions": restrictions, "user_intent": authorization.to_dict(), "trigger": source,
    })
    provider = controller.provider_config()
    state = {
        "payload": payload, "session": agent.session_id, "agent": agent.agent_id,
        "tool_enabled": agent.registry.is_enabled(tool.name), "tool_identity": id(tool),
        "checker_identity": id(checker), "sandbox_identity": id(tool.sandbox),
        "controller_identity": id(controller), "revision": controller.revision,
        "approval": asdict(controller.config), "provider": asdict(provider) if provider else None,
    }
    # Only the hash is retained; provider keys never enter the review payload/log.
    binding = hashlib.sha256(canonical(state).encode("utf-8")).hexdigest()
    complete = authorization.complete and any(r["source"] == "direct_user" for r in authorization.records)
    return ReviewRequest(payload, binding, complete)
