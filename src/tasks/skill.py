"""
Skill Registry - 借鉴 s05_skill_loading.py

两层技能模型：
1. 技能目录（便宜，放在 system prompt）
2. 完整技能内容（按需加载）

扫描 skills/ 目录下的 SKILL.md 文件。
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from src.infra.config import WORKSPACE_DIR
WORKDIR = Path(WORKSPACE_DIR)


SKILLS_DIR = WORKDIR / "skills"


@dataclass
class SkillDocument:
    name: str
    description: str
    frontmatter: dict
    full_path: Path


@dataclass
class SkillManifest:
    name: str
    description: str
    category: str
    file_path: str


class SkillRegistry:
    """
    技能注册表 - 管理所有可用技能
    """

    def __init__(self, skills_dir: Path = None):
        self.skills_dir = skills_dir or SKILLS_DIR
        self._catalog: dict[str, SkillManifest] = {}
        self._cache: dict[str, str] = {}
        self._load_catalog()

    def _load_catalog(self):
        """扫描并加载技能目录"""
        if not self.skills_dir.exists():
            return

        for skill_path in self.skills_dir.rglob("SKILL.md"):
            try:
                content = skill_path.read_text(encoding="utf-8")
                manifest = self._parse_skill(skill_path, content)
                if manifest:
                    self._catalog[manifest.name] = manifest
            except Exception:
                pass

    def _parse_skill(self, path: Path, content: str) -> SkillManifest | None:
        """解析 SKILL.md 文件，提取 frontmatter 和描述"""
        # 提取 frontmatter
        fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not fm_match:
            return None

        fm_text = fm_match.group(1)
        fm = {}
        for line in fm_text.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                fm[key.strip()] = value.strip()

        name = fm.get("name", path.parent.name)
        description = fm.get("description", "")
        category = fm.get("category", "general")

        return SkillManifest(
            name=name,
            description=description,
            category=category,
            file_path=str(path),
        )

    def load_full_text(self, name: str) -> str | None:
        """
        加载完整技能内容。
        返回 <skill> XML 标签格式的文本。
        """
        if name in self._cache:
            return self._cache[name]

        manifest = self._catalog.get(name)
        if not manifest:
            return None

        try:
            content = Path(manifest.file_path).read_text(encoding="utf-8")
            skill_xml = f"<skill>\n{content}\n</skill>"
            self._cache[name] = skill_xml
            return skill_xml
        except Exception:
            return None

    def describe_available(self) -> str:
        """
        生成技能目录描述，用于 system prompt。
        """
        if not self._catalog:
            return "无可用技能"

        lines = ["【可用技能】"]
        for name, manifest in sorted(self._catalog.items()):
            lines.append(f"- {manifest.name}: {manifest.description}")
        return "\n".join(lines)

    def get_catalog(self) -> list[SkillManifest]:
        """获取所有技能清单"""
        return list(self._catalog.values())


# 全局单例
_skill_registry: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    global _skill_registry
    if _skill_registry is None:
        _skill_registry = SkillRegistry()
    return _skill_registry


from dataclasses import dataclass
__all__ = ["SkillRegistry", "SkillManifest", "SkillDocument", "get_skill_registry"]