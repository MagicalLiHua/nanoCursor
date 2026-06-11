from __future__ import annotations

from src.api.services.compaction_policy_service import decide_compaction
from src.api.services.context_ledger_service import ContextSection, build_context_ledger
from src.api.services.model_context_registry_service import ModelContextSpec
from src.api.services.summary_compaction_service import summary_compact_ledger


def _summary_pressure_ledger():
    spec = ModelContextSpec(provider="test", model="tiny", context_window=1_200, max_output_tokens=120)
    return build_context_ledger(
        [
            ContextSection(
                id="current_user_message",
                label="Current User Message",
                category="current",
                tokens=100,
                compactible=False,
                priority=100,
            ),
            ContextSection(
                id="current_plan",
                label="Current Plan",
                category="plan",
                tokens=100,
                compactible=False,
                priority=100,
            ),
            ContextSection(
                id="tool_results",
                label="Tool Results",
                category="tool",
                tokens=620,
                compactible=True,
                priority=20,
            ),
            ContextSection(
                id="old_agent_activity",
                label="Old Agent Activity",
                category="history",
                tokens=420,
                compactible=True,
                priority=25,
            ),
        ],
        spec,
        conversation_id="conv-1",
        run_id="run-1",
    )


def test_summary_compaction_replaces_noisy_sections_with_summary():
    ledger = _summary_pressure_ledger()
    result = summary_compact_ledger(ledger, decision=decide_compaction(ledger))

    assert result.compacted is True
    assert result.after_tokens < result.before_tokens
    assert result.summary_tokens < result.source_tokens
    assert result.source_section_ids == ["tool_results"]
    assert "current_user_message" in result.preserved_anchors
    assert "current_plan" in result.preserved_anchors

    compacted = result.ledger
    assert compacted is not None
    assert {section.id for section in compacted.sections} >= {
        "current_user_message",
        "current_plan",
        "compacted_summary",
    }
    summary_section = next(section for section in compacted.sections if section.id == "compacted_summary")
    assert summary_section.detail["source_section_ids"] == ["tool_results"]
    assert "Tool Results" in summary_section.detail["summary"]


def test_summary_compaction_uses_injected_summarizer():
    ledger = _summary_pressure_ledger()

    def summarizer(sections, _ledger):
        return "Compressed facts: " + ", ".join(section.id for section in sections)

    result = summary_compact_ledger(
        ledger,
        decision=decide_compaction(ledger),
        mode="llm",
        summarizer=summarizer,
    )

    assert result.used_llm is True
    assert result.summary.startswith("Compressed facts")
    assert result.compacted is True


def test_summary_compaction_falls_back_when_injected_summarizer_fails():
    ledger = _summary_pressure_ledger()

    def failing_summarizer(_sections, _ledger):
        raise RuntimeError("provider unavailable")

    result = summary_compact_ledger(
        ledger,
        decision=decide_compaction(ledger),
        mode="llm",
        summarizer=failing_summarizer,
    )

    assert result.compacted is True
    assert result.used_llm is False
    assert result.warnings == ["summary provider fallback: provider unavailable"]
    assert result.summary.startswith("Context compaction summary")
