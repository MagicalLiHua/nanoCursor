"""Deterministic guard layer for Intent Router V3.

The guard layer owns product and safety invariants that should not be delegated
to an LLM. It does not try to understand every user request; it only marks hard
decisions and safety hints that the normalizer must respect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.api.models import IntentDecision


@dataclass
class IntentGuardResult:
    """Result of deterministic pre-routing guards."""

    hits: list[str] = field(default_factory=list)
    hard_decision: IntentDecision | None = None
    risk_level: str = "low"
    risk_reasons: list[str] = field(default_factory=list)

    @property
    def is_hard(self) -> bool:
        return self.hard_decision is not None


HARD_GUARD_ROUTES = {
    "direct_answer",
    "clarification_needed",
    "risky_operation",
}


def evaluate_intent_guards(
    prompt: str,
    deterministic_decision: IntentDecision,
) -> IntentGuardResult:
    """Evaluate deterministic safety and product guards.

    The current deterministic router already captures many signals. This layer
    converts those signals into explicit guard metadata so future LLM routing
    can be audited without changing the public V2 decision shape.
    """
    signals = set(deterministic_decision.signals or deterministic_decision.indicators or [])
    hits: list[str] = []
    risk_reasons: list[str] = []
    risk_level = "low"

    if not str(prompt or "").strip():
        hits.append("empty_prompt")

    if deterministic_decision.route == "direct_answer":
        hits.append("direct_answer_guard")

    if deterministic_decision.route == "clarification_needed":
        hits.append("clarification_guard")

    if "high_risk_scope" in signals or deterministic_decision.requires_approval:
        hits.append("high_risk_guard")
        risk_level = "high"
        risk_reasons.append(deterministic_decision.rationale or "High-risk request detected by deterministic guard.")

    if deterministic_decision.requires_workspace_write:
        hits.append("workspace_write_requested")
    if deterministic_decision.requires_shell:
        hits.append("shell_requested")

    hard_decision = deterministic_decision if deterministic_decision.route in HARD_GUARD_ROUTES else None
    if hard_decision:
        hard_decision.guard_hits = _unique(hits)
        hard_decision.risk_level = risk_level
        hard_decision.risk_reasons = _unique(risk_reasons)
        hard_decision.normalized_from = hard_decision.normalized_from or "deterministic_guard"

    return IntentGuardResult(
        hits=_unique(hits),
        hard_decision=hard_decision,
        risk_level=risk_level,
        risk_reasons=_unique(risk_reasons),
    )


def guard_payload(result: IntentGuardResult) -> dict[str, Any]:
    """Serialize guard metadata for persistence."""
    return {
        "hits": result.hits,
        "risk_level": result.risk_level,
        "risk_reasons": result.risk_reasons,
        "hard": result.is_hard,
    }


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result
