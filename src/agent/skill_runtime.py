"""Skill runtime — select relevant skills and inject them into the agent prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.api.services.skill_manifest_service import parse_skill_manifest


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
    ws = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    skills_dir = ws / ".nanocursor" / "skills"
    if not skills_dir.exists():
        return []

    prompt_lower = prompt.lower()
    team_roles: set[str] = set()
    for member in (team or []):
        role = str(member.get("role", "")).lower()
        if role:
            team_roles.add(role)

    selected: list[dict[str, Any]] = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue

        manifest = parse_skill_manifest(content)
        name = str(manifest.get("name", skill_dir.name)).lower()
        description = str(manifest.get("description", "")).lower()
        agents = manifest.get("agents", [])

        score = 0
        # Keyword match in name/description
        for keyword in prompt_lower.split():
            if len(keyword) >= 3:
                if keyword in name or keyword in description:
                    score += 1
        # Team role match
        for agent_role in (str(a).lower() for a in (agents if isinstance(agents, list) else [])):
            if agent_role in team_roles:
                score += 2

        if score > 0:
            selected.append({
                "skill_id": skill_dir.name,
                "name": name,
                "description": description,
                "path": str(skill_md),
                "score": score,
                "manifest": manifest,
            })

    selected.sort(key=lambda s: s["score"], reverse=True)
    # Cap at 5 skills to avoid prompt bloat
    return selected[:5]


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
        line = f"- **{name}**: {desc}"
        if len(line) > budget:
            break
        lines.append(line)
        budget -= len(line)

    instruction = "\n".join(lines)
    if len(instruction) > max_chars:
        instruction = instruction[:max_chars]
    return instruction
