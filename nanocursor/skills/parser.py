from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

log = logging.getLogger(__name__)

VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")
VALID_MODES = {"inline", "fork"}
VALID_CONTEXTS = {"full", "recent", "none"}


class SkillParseError(ValueError):
    pass


@dataclass
class SkillDef:
    name: str
    description: str
    prompt_body: str = ""
    mode: Literal["inline", "fork"] = "inline"
    model: str | None = None
    context: Literal["full", "recent", "none"] = "full"
    provider: str | None = None
    tools: tuple[str, ...] | None = None
    metadata: dict = field(default_factory=dict)
    source_path: Path | None = None
    is_directory: bool = False


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    stripped = raw.lstrip()
    if not stripped.startswith("---"):
        raise SkillParseError("Missing YAML frontmatter (must start with ---)")

    closing = re.search(r"(?m)^---[ \t]*$", stripped[3:])
    if closing is None:
        raise SkillParseError("Unclosed YAML frontmatter (missing closing ---)")

    end = 3 + closing.start()
    yaml_block = stripped[3:end]
    body = stripped[3 + closing.end():].lstrip("\n")

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        raise SkillParseError(f"Invalid YAML in frontmatter: {e}") from e

    if not isinstance(meta, dict):
        raise SkillParseError("Frontmatter must be a YAML mapping")

    return meta, body


def _validate_meta(meta: dict, source: str = "") -> None:
    ctx = f" in {source}" if source else ""

    if "name" not in meta:
        raise SkillParseError(f"Missing required field 'name'{ctx}")
    if "description" not in meta:
        raise SkillParseError(f"Missing required field 'description'{ctx}")

    name = meta["name"]
    if not isinstance(name, str) or not VALID_NAME_RE.match(name):
        raise SkillParseError(
            f"Invalid skill name '{name}'{ctx}: "
            "must be lowercase letters, digits, and hyphens, starting with a letter"
        )

    mode = meta.get("mode", "inline")
    if not isinstance(mode, str) or mode not in VALID_MODES:
        raise SkillParseError(f"Invalid mode '{mode}'{ctx}: must be one of {VALID_MODES}")

    context = meta.get("context", "full")
    if not isinstance(context, str) or context not in VALID_CONTEXTS:
        raise SkillParseError(f"Invalid context '{context}'{ctx}: must be one of {VALID_CONTEXTS}")


    supported = {"name", "description", "mode", "context", "model", "provider", "tools",
                 "license", "compatibility", "metadata"}
    if "allowed-tools" in meta:
        raise SkillParseError(f"allowed-tools preapproval is unsupported{ctx}; use fork tools to restrict capabilities, never grant permission")
    unknown = set(meta) - supported
    if unknown:
        raise SkillParseError(f"Unsupported fields {sorted(map(str, unknown))}{ctx}; put descriptive custom fields in metadata")
    for key in ("description", "model", "provider", "license", "compatibility"):
        if key in meta and (not isinstance(meta[key], str) or not meta[key].strip()):
            raise SkillParseError(f"{key} must be a nonempty string{ctx}")
    if "metadata" in meta and not isinstance(meta["metadata"], dict):
        raise SkillParseError(f"metadata must be a mapping{ctx}")
    if "tools" in meta:
        tools = meta["tools"]
        if (not isinstance(tools, list) or any(not isinstance(t, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in tools)):
            raise SkillParseError(f"tools must be a list of exact registered tool names{ctx}; [] means no tools")
    if mode == "inline":
        if meta.get("model", "inherit") != "inherit" or "provider" in meta or "tools" in meta:
            raise SkillParseError(f"inline inherits the main model and tools{ctx}; use mode: fork for overrides")
        if context != "full":
            raise SkillParseError(f"inline does not copy context{ctx}; omit context or use legacy full")


def from_metadata(meta: dict, body: str, path: Path, *, directory: bool = False) -> SkillDef:
    _validate_meta(meta, str(path))
    return SkillDef(
        name=meta["name"], description=meta["description"], prompt_body=body,
        mode=meta.get("mode", "inline"), model=meta.get("model"),
        provider=meta.get("provider"), context=meta.get("context", "full"),
        tools=tuple(dict.fromkeys(meta["tools"])) if "tools" in meta else None,
        metadata={k: meta[k] for k in ("metadata", "license", "compatibility") if k in meta},
        source_path=path, is_directory=directory,
    )


def parse_skill_file(path: Path) -> SkillDef:
    try:
        raw = path.read_text(encoding="utf-8")
        if path.name == "skill.yaml":
            meta = yaml.safe_load(raw)
            if not isinstance(meta, dict):
                raise SkillParseError(f"{path}: skill.yaml must be a mapping")
            body = (path.parent / "prompt.md").read_text(encoding="utf-8")
            meta.setdefault("name", path.parent.name.lower().replace(" ", "-"))
            meta.setdefault("description", next((line.strip() for line in body.splitlines()
                                                if line.strip() and not line.startswith(("#", "---"))), ""))
        else:
            meta, body = parse_frontmatter(raw)
        return from_metadata(meta, body, path, directory=path.name in {"SKILL.md", "skill.yaml"})
    except (OSError, UnicodeError, yaml.YAMLError) as e:
        raise SkillParseError(f"Cannot read skill file {path}: {e}") from e


def substitute_arguments(prompt_body: str, args: str) -> str:
    """将 $ARGUMENTS 占位符替换为用户请求（对齐 Go 版 promptHandler 逻辑）。

    若 prompt_body 中不含 $ARGUMENTS 占位符且 args 非空，
    则将用户请求追加到末尾（append fallback）。
    """
    if "$ARGUMENTS" in prompt_body:
        return prompt_body.replace("$ARGUMENTS", args)
    # 无占位符时的 append fallback
    if args.strip():
        return prompt_body + "\n\n## User Request\n\n" + args
    return prompt_body
