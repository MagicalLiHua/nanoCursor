from __future__ import annotations

from src.api.services.token_estimator_service import (
    estimate_json_tokens,
    estimate_section_tokens,
    estimate_tokens,
)


def test_token_estimator_handles_empty_and_non_empty_text():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello") >= 1


def test_token_estimator_uses_different_ratios():
    english = estimate_tokens("a" * 120, content_type="english")
    code = estimate_tokens("a" * 120, content_type="code")
    chinese = estimate_tokens("你好" * 60, content_type="mixed")

    assert code > english
    assert chinese > english


def test_token_estimator_handles_json_and_sections():
    value = {"message": "你好", "items": [1, 2, 3]}

    assert estimate_json_tokens(value) > 0
    assert estimate_section_tokens({"category": "json", "content": value}) > 0
