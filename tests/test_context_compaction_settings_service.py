from __future__ import annotations

from src.api.services.context_compaction_settings_service import (
    ContextCompactionSettings,
    get_context_compaction_settings,
    save_context_compaction_settings,
    should_auto_compact_level,
)


def test_context_compaction_settings_defaults_and_persistence(tmp_path):
    settings = get_context_compaction_settings(tmp_path)

    assert settings.summary_mode == "deterministic"
    assert settings.auto_compact_enabled is True
    assert settings.auto_compact_min_level == "hard"

    saved = save_context_compaction_settings({"summary_mode": "llm", "auto_compact_min_level": "emergency"}, tmp_path)

    assert saved.summary_mode == "llm"
    assert saved.auto_compact_min_level == "emergency"
    assert get_context_compaction_settings(tmp_path).summary_mode == "llm"


def test_should_auto_compact_level_respects_settings():
    hard_settings = ContextCompactionSettings(auto_compact_min_level="hard")
    emergency_settings = ContextCompactionSettings(auto_compact_min_level="emergency")
    disabled_settings = ContextCompactionSettings(auto_compact_enabled=False)

    assert should_auto_compact_level("hard", hard_settings) is True
    assert should_auto_compact_level("emergency", hard_settings) is True
    assert should_auto_compact_level("soft", hard_settings) is False
    assert should_auto_compact_level("hard", emergency_settings) is False
    assert should_auto_compact_level("emergency", emergency_settings) is True
    assert should_auto_compact_level("emergency", disabled_settings) is False
