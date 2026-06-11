"""Context usage ledger for runs and conversations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.agent.context_pack import ContextPack
from src.api.services.model_context_registry_service import ModelContextSpec
from src.api.services.token_estimator_service import estimate_json_tokens, estimate_tokens


ContextStatus = Literal["ok", "watch", "soft_compact", "hard_compact", "emergency"]


class ContextSection(BaseModel):
    id: str
    label: str
    category: str
    tokens: int = Field(ge=0)
    ratio: float = Field(default=0, ge=0)
    priority: int = Field(default=50, ge=0, le=100)
    compactible: bool = True
    source: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ContextLedger(BaseModel):
    conversation_id: str | None = None
    run_id: str | None = None
    turn_id: str | None = None
    provider: str
    model: str
    context_window: int
    max_output_tokens: int
    reserved_output_tokens: int
    usable_input_tokens: int
    input_tokens: int
    usage_ratio: float
    status: ContextStatus
    sections: list[ContextSection]
    created_at: float = Field(default_factory=time.time)


def build_context_ledger(
    sections: list[ContextSection | dict[str, Any]],
    model_spec: ModelContextSpec,
    *,
    conversation_id: str | None = None,
    run_id: str | None = None,
    turn_id: str | None = None,
    reserved_output_tokens: int | None = None,
) -> ContextLedger:
    normalized_sections = [
        section if isinstance(section, ContextSection) else ContextSection(**section)
        for section in sections
    ]
    input_tokens = sum(max(int(section.tokens), 0) for section in normalized_sections)
    reserved = min(
        int(reserved_output_tokens or model_spec.max_output_tokens),
        max(int(model_spec.context_window) - 1, 1),
    )
    usable_input = max(int(model_spec.context_window) - reserved, 1)
    usage_ratio = input_tokens / usable_input
    for section in normalized_sections:
        section.ratio = section.tokens / usable_input if usable_input else 0
    return ContextLedger(
        conversation_id=conversation_id,
        run_id=run_id,
        turn_id=turn_id,
        provider=model_spec.provider,
        model=model_spec.model,
        context_window=model_spec.context_window,
        max_output_tokens=model_spec.max_output_tokens,
        reserved_output_tokens=reserved,
        usable_input_tokens=usable_input,
        input_tokens=input_tokens,
        usage_ratio=usage_ratio,
        status=_status_for_ratio(usage_ratio, model_spec),
        sections=normalized_sections,
    )


def sections_from_context_pack(pack: ContextPack) -> list[ContextSection]:
    data = pack.to_dict()
    section_specs = [
        ("task_summary", "Task", "current", data.get("task_summary"), 95, False, "mixed"),
        ("conversation_summary", "Conversation Summary", "history", data.get("conversation_summary"), 70, True, "mixed"),
        ("execution_summary", "Execution Summary", "run", data.get("execution_summary"), 75, True, "mixed"),
        ("workspace_summary", "Workspace Summary", "project", data.get("workspace_summary"), 70, True, "json"),
        ("relevant_files", "Relevant Files", "project", data.get("relevant_files"), 65, True, "json"),
        ("selected_files", "Selected Files", "files", data.get("selected_files"), 80, True, "json"),
        ("recent_changes", "Recent Changes", "diff", data.get("recent_changes"), 80, True, "json"),
        ("file_outlines", "File Outlines", "project", data.get("file_outlines"), 65, True, "json"),
        ("symbols", "Symbols", "project", data.get("symbols"), 55, True, "json"),
        ("recent_failures", "Recent Failures", "recovery", data.get("recent_failures"), 90, True, "json"),
        ("recovery_context", "Recovery Context", "recovery", data.get("recovery_context"), 95, False, "json"),
        ("user_preferences", "User Preferences", "memory", data.get("user_preferences"), 75, True, "json"),
        ("selected_memories", "Selected Memories", "memory", data.get("selected_memories"), 70, True, "json"),
        ("selected_skill_details", "Skills", "skills", data.get("selected_skill_details"), 60, True, "json"),
        ("current_plan", "Current Plan", "plan", data.get("current_plan"), 95, False, "json"),
        ("turn_context", "Turn Context", "turn", data.get("turn_context"), 95, False, "json"),
        ("tool_policy", "Tool Policy", "policy", data.get("tool_policy"), 100, False, "json"),
        ("selection_reasons", "Selection Reasons", "debug", data.get("selection_reasons"), 45, True, "json"),
        ("omitted", "Omitted Context", "debug", data.get("omitted"), 35, True, "json"),
    ]
    sections: list[ContextSection] = []
    for section_id, label, category, value, priority, compactible, content_type in section_specs:
        tokens = _tokens_for_value(value, content_type)
        if tokens <= 0:
            continue
        sections.append(
            ContextSection(
                id=section_id,
                label=label,
                category=category,
                tokens=tokens,
                priority=priority,
                compactible=compactible,
                source="context_pack",
                detail={"content_type": content_type},
            )
        )
    return sections


def save_context_ledger(ledger: ContextLedger, workspace_dir: str | Path) -> ContextLedger:
    for path in _ledger_paths(ledger, workspace_dir):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return ledger


def load_latest_context_ledger(
    workspace_dir: str | Path,
    *,
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> ContextLedger | None:
    for path in _candidate_ledger_paths(workspace_dir, conversation_id=conversation_id, run_id=run_id):
        if not path.exists():
            continue
        try:
            return ContextLedger(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return None


def _tokens_for_value(value: Any, content_type: str) -> int:
    if value in (None, "", [], {}):
        return 0
    if isinstance(value, str):
        return estimate_tokens(value, content_type=content_type)
    return estimate_json_tokens(value)


def _status_for_ratio(ratio: float, spec: ModelContextSpec) -> ContextStatus:
    if ratio >= spec.emergency_ratio:
        return "emergency"
    if ratio >= spec.hard_compact_ratio:
        return "hard_compact"
    if ratio >= spec.soft_compact_ratio:
        return "soft_compact"
    if ratio >= spec.watch_ratio:
        return "watch"
    return "ok"


def _ledger_paths(ledger: ContextLedger, workspace_dir: str | Path) -> list[Path]:
    workspace = Path(workspace_dir)
    paths = [workspace / ".nanocursor" / "context" / "context_ledger_latest.json"]
    if ledger.run_id:
        paths.append(workspace / ".nanocursor" / "runs" / ledger.run_id / "context_ledger.json")
    if ledger.conversation_id:
        paths.append(
            workspace
            / ".nanocursor"
            / "conversations"
            / ledger.conversation_id
            / "context_ledger_latest.json"
        )
    return paths


def _candidate_ledger_paths(
    workspace_dir: str | Path,
    *,
    conversation_id: str | None,
    run_id: str | None,
) -> list[Path]:
    workspace = Path(workspace_dir)
    paths: list[Path] = []
    if run_id:
        paths.append(workspace / ".nanocursor" / "runs" / run_id / "context_ledger.json")
    if conversation_id:
        paths.append(
            workspace
            / ".nanocursor"
            / "conversations"
            / conversation_id
            / "context_ledger_latest.json"
        )
    paths.append(workspace / ".nanocursor" / "context" / "context_ledger_latest.json")
    return paths
