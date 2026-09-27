from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable

import yaml

from nanocursor.runtime import app_home
from nanocursor.skills.parser import SkillDef, SkillParseError, parse_frontmatter, parse_skill_file

PROJECT_SKILLS_DIR = ".nanocursor/skills"
USER_SKILLS_DIR = "~/.nanocursor/skills"


class SkillLoader:
    """Reload original sources, with project errors shadowing user definitions."""

    def __init__(self, work_dir: str) -> None:
        self._work_dir = work_dir
        self._project_dir = Path(work_dir) / PROJECT_SKILLS_DIR
        self._user_dir = app_home() / "skills"
        self._skills: dict[str, SkillDef] = {}
        self._claims: dict[Path, str] = {}
        self.diagnostics: dict[str, str] = {}
        self.validator: Callable[[SkillDef], object] | None = None
        self._fingerprint: tuple = ()

    def _sources(self, directory: Path) -> list[Path]:
        if not directory.is_dir():
            return []
        sources = []
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.suffix == ".md":
                sources.append(entry)
            elif entry.is_dir():
                # An invalid YAML definition must not fall back to another format.
                for name in ("skill.yaml", "SKILL.md"):
                    if (entry / name).exists():
                        sources.append(entry / name)
                        break
        return sources

    def _claim(self, path: Path) -> str:
        fallback = self._claims.get(path, path.parent.name if path.name in {"skill.yaml", "SKILL.md"} else path.stem)
        try:
            raw = path.read_text(encoding="utf-8")
            meta = yaml.safe_load(raw) if path.name == "skill.yaml" else parse_frontmatter(raw)[0]
            name = meta.get("name") if isinstance(meta, dict) else None
            return name if isinstance(name, str) and name else fallback
        except (OSError, UnicodeError, yaml.YAMLError, SkillParseError):
            return fallback

    def load_all(self) -> dict[str, SkillDef]:
        skills, diagnostics, seen = {}, {}, set()
        for directory in (self._project_dir, self._user_dir):
            try:
                sources = self._sources(directory)
            except OSError as exc:
                diagnostics[str(directory)] = str(exc)
                # A failed higher-priority scan cannot authorize lower sources.
                break
            for path in sources:
                name = self._claim(path)
                if name in seen:
                    continue
                seen.add(name)
                self._claims[path] = name
                try:
                    skill = parse_skill_file(path)
                    skills[skill.name] = skill
                    if self.validator:
                        self.validator(skill)
                except (SkillParseError, ValueError, OSError) as exc:
                    diagnostics[name] = f"{path}: {exc}"
        self._skills, self.diagnostics = skills, diagnostics
        self._fingerprint = self._state()
        return {name: skill for name, skill in skills.items() if name not in diagnostics}

    @staticmethod
    def _parse_skill_yaml(yaml_path: Path, skill_dir: Path) -> SkillDef:
        return parse_skill_file(yaml_path)

    def _state(self) -> tuple:
        result = []
        for root in (self._project_dir, self._user_dir):
            try:
                paths = [root, *self._sources(root)]
                paths += [p.parent / "prompt.md" for p in paths if p.name == "skill.yaml"]
                for path in paths:
                    try:
                        stat = path.stat()
                        result.append((str(path), stat.st_ino, stat.st_mtime_ns, stat.st_size))
                    except OSError:
                        result.append((str(path), None))
            except OSError:
                result.append((str(root), None))
        return tuple(result)

    def get(self, name: str) -> SkillDef | None:
        previous = self._skills.get(name)
        if previous and previous.source_path is None:
            return copy.deepcopy(previous)
        self.load_all()
        current = self._skills.get(name) if name not in self.diagnostics else None
        if previous and current and previous.source_path != current.source_path:
            self.diagnostics[name] = f"{previous.source_path}: source disappeared or changed; reload and select the new definition explicitly"
            self._skills.pop(name, None)
            return None
        return copy.deepcopy(current)

    def get_catalog(self) -> list[tuple[str, str]]:
        if self.needs_reload():
            self.load_all()
        # Permissions and MCP connectivity can change without changing a file.
        catalog = []
        for skill in self._skills.values():
            try:
                if self.validator:
                    self.validator(skill)
            except (ValueError, OSError) as exc:
                self.diagnostics[skill.name] = f"{skill.source_path}: {exc}"
                continue
            self.diagnostics.pop(skill.name, None)
            catalog.append((skill.name, skill.description))
        return catalog

    def needs_reload(self) -> bool:
        return self._state() != self._fingerprint

    def reload(self) -> dict[str, SkillDef]:
        return self.load_all()

    def get_source_label(self, name: str) -> str:
        skill = self._skills.get(name)
        if not skill or skill.source_path is None:
            return "builtin" if skill else "unknown"
        if skill.source_path.is_relative_to(self._project_dir):
            return "project"
        if skill.source_path.is_relative_to(self._user_dir):
            return "user"
        return "unknown"
