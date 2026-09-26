from unittest.mock import AsyncMock

import pytest

from nanocursor.config import ProviderConfig
from nanocursor.eval.approval import CASES_PATH, load_cases, review_case, summarize
from nanocursor.tools.bash import Bash


@pytest.mark.asyncio
async def test_replay_never_executes_samples_or_calls_model_in_dry_run(monkeypatch):
    execute = AsyncMock(side_effect=AssertionError("A replay must never execute commands"))
    model = AsyncMock(side_effect=AssertionError("A dry run must never call the model"))
    monkeypatch.setattr(Bash, "execute", execute)
    monkeypatch.setattr("nanocursor.permissions.reviewer.complete_review", model)
    provider = ProviderConfig("dry-run", "openai-compat", "https://invalid.example", "fake")
    cases = load_cases(CASES_PATH)
    assert 40 <= len(cases) <= 60
    results = [await review_case(case, provider, 10, True) for case in cases]
    report = summarize(results)
    assert report["unsafe_allows"] == 0
    assert report["pending_model"] > 0
    assert report["p50_seconds"] is None
    execute.assert_not_awaited()
    model.assert_not_awaited()


def test_summary_separates_unsafe_allows_from_conservative_errors():
    defaults = {"source": "model_manual", "elapsed": 1, "input_tokens": 10, "output_tokens": 10}
    results = [
        dict(defaults, id="risky", expected="manual", actual="allow"),
        dict(defaults, id="risky", expected="manual", actual="manual"),
        dict(defaults, id="safe", expected="allow", actual="manual"),
    ]
    report = summarize(results)
    assert report["unsafe_allows"] == 1
    assert report["safe_cases_sent_to_human"] == 1
    assert report["inconsistent_cases"] == 1
