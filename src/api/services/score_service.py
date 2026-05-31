"""Delivery scoring service for nanoCursor runs."""

from __future__ import annotations

from typing import Any

from src.api.services.quality_service import build_quality_gate


REQUIRED_FAILED_IMPACT = 18
REQUIRED_WARNING_IMPACT = 10
RECOMMENDED_WARNING_IMPACT = 5


def _impact_for(check: dict[str, Any]) -> int:
    status = check.get("status")
    severity = check.get("severity")

    if status == "failed":
        return REQUIRED_FAILED_IMPACT if severity == "required" else REQUIRED_WARNING_IMPACT
    if status == "warning":
        return REQUIRED_WARNING_IMPACT if severity == "required" else RECOMMENDED_WARNING_IMPACT
    return 0


def _level_for(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "warning"
    return "failed"


def build_delivery_score(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Build a stable, explainable delivery score from the quality gate."""
    quality = build_quality_gate(thread_id, workspace_dir)
    reasons: list[dict[str, Any]] = []
    score = 100

    for check in quality["checks"]:
        impact = _impact_for(check)
        if impact <= 0:
            continue

        score -= impact
        reasons.append(
            {
                "id": check["id"],
                "label": check["label"],
                "impact": impact,
                "detail": check.get("detail", ""),
            }
        )

    score = max(0, min(100, score))
    if quality["status"] == "failed":
        score = min(score, 59)
    elif quality["status"] == "warning":
        score = min(score, 84)

    return {
        "thread_id": quality["thread_id"],
        "workspace_dir": quality["workspace_dir"],
        "score": score,
        "level": _level_for(score),
        "quality_status": quality["status"],
        "passed_count": quality["passed_count"],
        "warning_count": quality["warning_count"],
        "failed_count": quality["failed_count"],
        "reasons": reasons,
        "quality": quality,
    }
