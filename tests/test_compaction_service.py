from __future__ import annotations

from src.api.services.compaction_policy_service import decide_compaction
from src.api.services.compaction_service import compact_context_ledger, compact_ledger
from src.api.services.context_ledger_service import (
    ContextSection,
    build_context_ledger,
    save_context_ledger,
)
from src.api.services.model_context_registry_service import ModelContextSpec


def _pressure_ledger():
    spec = ModelContextSpec(provider="test", model="model", context_window=1_000, max_output_tokens=100)
    return build_context_ledger(
        [
            ContextSection(id="current_user_message", label="Current", category="current", tokens=80, compactible=False, priority=100),
            ContextSection(id="tool_results", label="Tools", category="tool", tokens=720, compactible=True, priority=20),
            ContextSection(id="current_plan", label="Plan", category="plan", tokens=80, compactible=False, priority=100),
        ],
        spec,
        conversation_id="conv-1",
        run_id="run-1",
    )


def test_compaction_service_reduces_tokens_and_preserves_anchors():
    ledger = _pressure_ledger()
    result = compact_ledger(ledger, decision=decide_compaction(ledger))

    assert result.compacted is True
    assert result.after_tokens < result.before_tokens
    assert "current_user_message" in result.preserved_anchors
    assert "current_plan" in result.preserved_anchors
    assert "tool_results" in result.updated_sections


def test_compaction_service_persists_result_and_history(tmp_path):
    ledger = _pressure_ledger()
    save_context_ledger(ledger, tmp_path)

    result = compact_context_ledger(tmp_path, run_id="run-1", level="hard", reason="test")

    assert result.compacted is True
    assert (tmp_path / ".nanocursor" / "runs" / "run-1" / "context_ledger.json").exists()
    assert (tmp_path / ".nanocursor" / "runs" / "run-1" / "compaction_history.jsonl").exists()


def test_compaction_service_supports_summary_strategy(tmp_path):
    ledger = _pressure_ledger()
    save_context_ledger(ledger, tmp_path)

    result = compact_context_ledger(
        tmp_path,
        run_id="run-1",
        level="hard",
        reason="test",
        strategy="summary",
        summary_mode="deterministic",
    )

    assert result.compacted is True
    assert result.strategy == "summary"
    assert result.summary["source_section_ids"] == ["tool_results"]
    assert result.ledger is not None
    assert any(section.id == "compacted_summary" for section in result.ledger.sections)
