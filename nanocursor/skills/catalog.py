from __future__ import annotations


MAX_DESCRIPTION_CHARS = 240
MAX_CATALOG_CHARS = 6000


def format_skill_catalog(catalog: list[tuple[str, str]]) -> str:
    """Keep discovery hints bounded; LoadSkill provides the full body on demand."""
    if not catalog:
        return ""

    header = "You can use the following Skills:\n\n"
    footer = "\nIf the user's request matches a Skill, call LoadSkill to activate it."
    lines: list[str] = []
    used = len(header) + len(footer)
    for name, description in catalog:
        summary = " ".join(description.split())
        if len(summary) > MAX_DESCRIPTION_CHARS:
            summary = summary[:MAX_DESCRIPTION_CHARS - 3].rstrip() + "..."
        line = f"- {name}: {summary}"
        # Reserve space for an omitted-count line when the catalog is full.
        if used + len(line) + 1 + 64 > MAX_CATALOG_CHARS:
            break
        lines.append(line)
        used += len(line) + 1

    omitted = len(catalog) - len(lines)
    if omitted:
        lines.append(f"- {omitted} more Skills omitted from this catalog.")
    return header + "\n".join(lines) + "\n" + footer
