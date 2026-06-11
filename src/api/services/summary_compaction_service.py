"""Summary-based context compaction.

The deterministic compactor reduces token counts per section. This module adds
a stronger strategy: replace low-priority, compactible historical sections with
a compact summary while preserving current-turn anchors.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from src.api.services.compaction_policy_service import CompactionDecision, decide_compaction
from src.api.services.context_ledger_service import (
    ContextLedger,
    ContextSection,
    build_context_ledger,
)
from src.api.services.model_context_registry_service import ModelContextSpec
from src.api.services.token_estimator_service import estimate_tokens

SummaryMode = Literal["deterministic", "llm"]
SummaryCallable = Callable[[list[ContextSection], ContextLedger], str]

SUMMARY_TARGET_CATEGORIES = {
    "history",
    "tool",
    "debug",
    "run",
    "memory",
    "diff",
    "project",
    "files",
    "skills",
    "mcp",
}


class SummaryCompactionResult(BaseModel):
    compacted: bool
    mode: SummaryMode
    used_llm: bool = False
    before_tokens: int
    after_tokens: int
    reduced_tokens: int
    source_tokens: int = 0
    summary_tokens: int = 0
    summary: str = ""
    source_section_ids: list[str] = Field(default_factory=list)
    preserved_anchors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ledger: ContextLedger | None = None


def summary_compact_ledger(
    ledger: ContextLedger,
    *,
    decision: CompactionDecision | None = None,
    mode: SummaryMode = "deterministic",
    summarizer: SummaryCallable | None = None,
) -> SummaryCompactionResult:
    """Compact a ledger by summarizing older noisy sections.

    The optional ``summarizer`` hook makes the service testable and lets future
    runtime code plug in provider-specific LLM summarization without changing
    the public compaction API.
    """

    decision = decision or decide_compaction(ledger)
    preserved = _preserved_anchor_ids(ledger)
    if not decision.should_compact:
        return SummaryCompactionResult(
            compacted=False,
            mode=mode,
            before_tokens=ledger.input_tokens,
            after_tokens=ledger.input_tokens,
            reduced_tokens=0,
            preserved_anchors=preserved,
            warnings=[decision.reason],
            ledger=ledger,
        )

    candidates = _select_summary_candidates(ledger, target_tokens=decision.target_reduction_tokens)
    if not candidates:
        return SummaryCompactionResult(
            compacted=False,
            mode=mode,
            before_tokens=ledger.input_tokens,
            after_tokens=ledger.input_tokens,
            reduced_tokens=0,
            preserved_anchors=preserved,
            warnings=["no compactible sections were eligible for summary compaction"],
            ledger=ledger,
        )

    warnings: list[str] = []
    used_llm = False
    if summarizer is not None:
        try:
            summary = summarizer(candidates, ledger)
            used_llm = mode == "llm"
        except Exception as exc:
            summary = build_deterministic_summary(candidates, ledger)
            warnings.append(f"summary provider fallback: {exc}")
    elif mode == "llm":
        summary, used_llm, llm_warnings = _summarize_with_llm_or_fallback(candidates, ledger)
        warnings.extend(llm_warnings)
    else:
        summary = build_deterministic_summary(candidates, ledger)

    source_tokens = sum(section.tokens for section in candidates)
    summary = _fit_summary_budget(summary, max_tokens=_summary_budget(source_tokens, decision.target_reduction_tokens))
    summary_tokens = estimate_tokens(summary, content_type="mixed")

    if summary_tokens >= source_tokens:
        return SummaryCompactionResult(
            compacted=False,
            mode=mode,
            used_llm=used_llm,
            before_tokens=ledger.input_tokens,
            after_tokens=ledger.input_tokens,
            reduced_tokens=0,
            source_tokens=source_tokens,
            summary_tokens=summary_tokens,
            summary=summary,
            source_section_ids=[section.id for section in candidates],
            preserved_anchors=preserved,
            warnings=warnings + ["summary did not reduce tokens"],
            ledger=ledger,
        )

    candidate_ids = {section.id for section in candidates}
    summary_section = ContextSection(
        id="compacted_summary",
        label="Compacted Summary",
        category="summary",
        tokens=summary_tokens,
        priority=82,
        compactible=True,
        source="summary_compaction",
        detail={
            "mode": mode,
            "used_llm": used_llm,
            "source_section_ids": sorted(candidate_ids),
            "source_tokens": source_tokens,
            "summary": summary,
        },
    )
    sections = [section for section in ledger.sections if section.id not in candidate_ids]
    sections.append(summary_section)

    spec = ModelContextSpec(
        provider=ledger.provider,
        model=ledger.model,
        context_window=ledger.context_window,
        max_output_tokens=ledger.max_output_tokens,
        source="summary_compaction",
    )
    compacted = build_context_ledger(
        sections,
        spec,
        conversation_id=ledger.conversation_id,
        run_id=ledger.run_id,
        turn_id=ledger.turn_id,
        reserved_output_tokens=ledger.reserved_output_tokens,
    )
    reduced = max(ledger.input_tokens - compacted.input_tokens, 0)
    if decision.target_reduction_tokens and reduced < decision.target_reduction_tokens:
        warnings.append("summary compaction did not reach the target reduction")

    return SummaryCompactionResult(
        compacted=True,
        mode=mode,
        used_llm=used_llm,
        before_tokens=ledger.input_tokens,
        after_tokens=compacted.input_tokens,
        reduced_tokens=reduced,
        source_tokens=source_tokens,
        summary_tokens=summary_tokens,
        summary=summary,
        source_section_ids=sorted(candidate_ids),
        preserved_anchors=preserved,
        warnings=warnings,
        ledger=compacted,
    )


def build_deterministic_summary(sections: list[ContextSection], ledger: ContextLedger) -> str:
    lines = [
        "Context compaction summary:",
        f"- Conversation: {ledger.conversation_id or 'n/a'}; run: {ledger.run_id or 'n/a'}.",
        f"- Replaced {len(sections)} noisy context sections with this summary.",
    ]
    for section in sections[:12]:
        source_hint = section.source or section.category
        lines.append(
            f"- {section.label} ({section.id}, {section.category}, {section.tokens} tokens, source={source_hint})."
        )
    if len(sections) > 12:
        lines.append(f"- {len(sections) - 12} additional low-priority sections were omitted.")
    lines.append(
        "- Preserve current user request, current plan, turn context, recovery context "
        "and tool policy verbatim."
    )
    return "\n".join(lines)


def _select_summary_candidates(ledger: ContextLedger, *, target_tokens: int) -> list[ContextSection]:
    candidates = [
        section
        for section in ledger.sections
        if section.compactible
        and section.priority < 90
        and section.category in SUMMARY_TARGET_CATEGORIES
        and section.tokens > 0
    ]
    candidates.sort(key=lambda section: (section.priority, -section.tokens, section.id))
    selected: list[ContextSection] = []
    selected_tokens = 0
    minimum_target = max(target_tokens, int(ledger.input_tokens * 0.25))
    for section in candidates:
        selected.append(section)
        selected_tokens += section.tokens
        if selected_tokens >= minimum_target:
            break
    return selected


def _summary_budget(source_tokens: int, target_reduction_tokens: int) -> int:
    conservative_budget = max(80, source_tokens // 5)
    if target_reduction_tokens > 0:
        conservative_budget = min(conservative_budget, max(source_tokens - target_reduction_tokens, 80))
    return max(60, min(conservative_budget, 1_200))


def _fit_summary_budget(summary: str, *, max_tokens: int) -> str:
    if estimate_tokens(summary, content_type="mixed") <= max_tokens:
        return summary
    lines = [line for line in summary.splitlines() if line.strip()]
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join([*kept, line, "- Additional details were compressed."])
        if estimate_tokens(candidate, content_type="mixed") > max_tokens:
            break
        kept.append(line)
    if not kept:
        return summary[: max_tokens * 3].rstrip() + "\n- Additional details were compressed."
    return "\n".join([*kept, "- Additional details were compressed."])


def _summarize_with_llm_or_fallback(
    sections: list[ContextSection],
    ledger: ContextLedger,
) -> tuple[str, bool, list[str]]:
    prompt = "\n".join(
        [
            "Summarize these context sections for a coding agent. Keep facts, decisions, "
            "failures, file paths, and constraints.",
            "Do not invent details. Use compact bullet points.",
            "",
            build_deterministic_summary(sections, ledger),
        ]
    )
    try:
        from src.infra.llm_config import create_sync_client, get_model_name

        client = create_sync_client()
        response = client.messages.create(
            model=get_model_name(),
            max_tokens=800,
            system="You compress coding-agent context for later continuation.",
            messages=[{"role": "user", "content": prompt}],
        )
        content = getattr(response, "content", [])
        text_parts = [getattr(part, "text", "") for part in content if getattr(part, "text", "")]
        summary = "\n".join(text_parts).strip()
        if summary:
            return summary, True, []
    except Exception as exc:  # pragma: no cover - depends on local provider state.
        return build_deterministic_summary(sections, ledger), False, [f"llm summary fallback: {exc}"]
    return build_deterministic_summary(sections, ledger), False, ["llm summary returned empty content"]


def _preserved_anchor_ids(ledger: ContextLedger) -> list[str]:
    return [section.id for section in ledger.sections if not section.compactible or section.priority >= 90]
