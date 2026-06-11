from __future__ import annotations

from src.agent.context_pack import ContextPack
from src.api.services.context_ledger_service import (
    ContextSection,
    build_context_ledger,
    load_latest_context_ledger,
    save_context_ledger,
    sections_from_context_pack,
)
from src.api.services.model_context_registry_service import ModelContextSpec


def _spec() -> ModelContextSpec:
    return ModelContextSpec(
        provider="test",
        model="small",
        context_window=1_000,
        max_output_tokens=100,
    )


def test_context_ledger_builds_usage_statuses():
    ledger = build_context_ledger(
        [ContextSection(id="history", label="History", category="history", tokens=800)],
        _spec(),
        run_id="run-1",
    )

    assert ledger.run_id == "run-1"
    assert ledger.usable_input_tokens == 900
    assert ledger.input_tokens == 800
    assert ledger.status == "hard_compact"
    assert ledger.sections[0].ratio > 0


def test_context_ledger_persists_for_run_and_conversation(tmp_path):
    ledger = build_context_ledger(
        [ContextSection(id="current", label="Current", category="current", tokens=20, compactible=False)],
        _spec(),
        conversation_id="conv-1",
        run_id="run-1",
    )

    save_context_ledger(ledger, tmp_path)

    assert load_latest_context_ledger(tmp_path, run_id="run-1").run_id == "run-1"
    assert load_latest_context_ledger(tmp_path, conversation_id="conv-1").conversation_id == "conv-1"


def test_context_pack_sections_keep_current_plan_as_anchor():
    pack = ContextPack(
        task_summary="实现排序算法",
        conversation_summary="之前讨论了 Python 文件结构",
        current_plan=[{"title": "write code", "description": "create implementation"}],
        tool_policy={"mode": "medium"},
    )

    sections = sections_from_context_pack(pack)
    by_id = {section.id: section for section in sections}

    assert by_id["task_summary"].compactible is False
    assert by_id["current_plan"].compactible is False
    assert by_id["conversation_summary"].compactible is True
