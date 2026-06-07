"""Skill runtime — select relevant skills and inject them into the agent prompt."""

from __future__ import annotations

from typing import Any

from src.api.services.skill_registry_service import preview_skill_selection, get_skill


def select_skills_for_run(
    prompt: str,
    team: list[dict[str, Any]] | None = None,
    workspace_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Select workspace skills relevant to the user's prompt and team.

    Matches by:
      - Keyword overlap between prompt and skill name/description.
      - Team role names matching skill agent lists.
    """
    preview = preview_skill_selection(prompt, workspace_dir, team=team, max_skills=5)
    selected: list[dict[str, Any]] = []
    for item in preview.get("selected", []):
        try:
            detail = get_skill(str(item.get("id")), workspace_dir)
        except ValueError:
            continue
        selected.append({
            "skill_id": str(item.get("id", "")).replace("skill.", "", 1),
            "id": item.get("id"),
            "name": detail.get("name", item.get("name", "")),
            "description": detail.get("description", ""),
            "path": detail.get("path", ""),
            "score": item.get("score", 0),
            "selection_reasons": item.get("selection_reasons", []),
            "manifest": detail,
            "content": detail.get("content", ""),
            "tool_permissions": detail.get("tool_permissions", []),
            "context_budget": detail.get("context_budget", 1200),
        })
    return selected


def build_skill_instruction(skills: list[dict[str, Any]], max_chars: int = 2000) -> str:
    """Build a concise runtime instruction string from a list of selected skills.

    Stays within *max_chars* to respect the prompt token budget.
    """
    if not skills:
        return ""

    lines: list[str] = ["## 已加载的 Skills", ""]
    budget = max_chars
    for s in skills:
        name = s.get("name", s.get("skill_id", ""))
        desc = s.get("description", "")
        permissions = ", ".join(s.get("tool_permissions", [])[:6])
        reasons = "; ".join(s.get("selection_reasons", [])[:3])
        line = f"- **{name}**: {desc}"
        if permissions:
            line += f" | permissions: {permissions}"
        if reasons:
            line += f" | reason: {reasons}"
        if len(line) > budget:
            break
        lines.append(line)
        budget -= len(line)

    instruction = "\n".join(lines)
    if len(instruction) > max_chars:
        instruction = instruction[:max_chars]
    return instruction
