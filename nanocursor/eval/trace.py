from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EvalRunSummary:
    run_id: str
    task_id: str
    status: str
    started_at: str
    finished_at: str
    model: str
    max_turns: int
    max_wall_time_seconds: int
    turns_used: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    tool_errors: int
    final_response: str
    errors: list[str]


class TraceRecorder:
    def __init__(self, output_dir: Path, run_id: str) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = output_dir / f"{run_id}.trace.jsonl"
        self.summary_path = output_dir / f"{run_id}.summary.json"

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {"timestamp": utc_now(), "type": event_type, "payload": payload}
        with self.trace_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    def write_summary(self, summary: EvalRunSummary) -> None:
        self.summary_path.write_text(
            json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
