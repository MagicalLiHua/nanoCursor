from __future__ import annotations

from src.api.services.compaction_policy_service import decide_compaction
from src.api.services.context_ledger_service import ContextSection, build_context_ledger
from src.api.services.model_context_registry_service import ModelContextSpec


def _ledger(tokens: int):
    spec = ModelContextSpec(provider="test", model="model", context_window=1_000, max_output_tokens=100)
    return build_context_ledger(
        [ContextSection(id="history", label="History", category="history", tokens=tokens)],
        spec,
    )


def test_compaction_policy_does_not_compact_low_usage():
    decision = decide_compaction(_ledger(100))

    assert decision.should_compact is False
    assert decision.level == "none"


def test_compaction_policy_soft_hard_and_emergency_levels():
    soft = decide_compaction(_ledger(700))
    hard = decide_compaction(_ledger(800))
    emergency = decide_compaction(_ledger(850))

    assert soft.should_compact is True
    assert soft.level == "soft"
    assert hard.level == "hard"
    assert any(action.action == "refresh_execution_summary" for action in hard.actions)
    assert emergency.level == "emergency"
    assert any(action.action == "trim_selected_files" for action in emergency.actions)
