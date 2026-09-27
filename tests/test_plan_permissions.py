from pathlib import Path

import pytest

from nanocursor.permissions import (
    DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine,
)
from nanocursor.tools import create_default_registry


def checker_for(root: Path, *, effect: str = "") -> PermissionChecker:
    rules = root / "rules.yaml"
    rules.write_text(f'- rule: "WriteFile(*)"\n  effect: {effect}\n' if effect else "[]")
    return PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(root)), RuleEngine(user_rules_path=rules),
        mode=PermissionMode.PLAN,
    )


def decision(checker, path):
    return checker.check(create_default_registry().get("WriteFile"), {"file_path": str(path), "content": "plan"})


def test_only_allocated_plan_can_use_write_exception(tmp_path):
    checker = checker_for(tmp_path)
    checker.plan_file_path = str(tmp_path / ".nanocursor/plans/current.md")
    assert decision(checker, ".nanocursor/plans/current.md").effect == "allow"
    assert decision(checker, "src/current.md").effect == "ask"
    assert decision(checker, ".nanocursor/plans/other.md").effect == "ask"
    assert decision(checker, ".nanocursor/skills/.nanocursor/plans/current.md").source == "path_restriction"


def test_no_allocated_plan_means_no_path_shortcut(tmp_path):
    checker = checker_for(tmp_path)
    assert decision(checker, ".nanocursor/plans/current.md").effect == "ask"


@pytest.mark.parametrize("effect", ["deny", "ask"])
def test_explicit_restrictions_precede_allocated_plan(tmp_path, effect):
    checker = checker_for(tmp_path, effect=effect)
    checker.plan_file_path = str(tmp_path / ".nanocursor/plans/current.md")
    result = decision(checker, checker.plan_file_path)
    assert result.effect == effect
    assert result.source == f"explicit_{effect}"


def test_path_restriction_precedes_allocated_plan(tmp_path):
    checker = checker_for(tmp_path)
    checker.plan_file_path = str(tmp_path / ".nanocursor/skills/current.md")
    assert decision(checker, checker.plan_file_path).source == "path_restriction"


def test_symlink_is_not_hidden_by_parent_components(tmp_path):
    checker = checker_for(tmp_path)
    checker.plan_file_path = str(tmp_path / ".nanocursor/plans/current.md")
    target = tmp_path / "other/subdir"
    target.mkdir(parents=True)
    (tmp_path / "linked").symlink_to(target, target_is_directory=True)
    assert decision(checker, "linked/../.nanocursor/plans/current.md").effect == "ask"


@pytest.mark.parametrize("link_kind", ["file", "directory", "alias"])
def test_linked_plan_paths_lose_the_exception(tmp_path, link_kind):
    checker = checker_for(tmp_path)
    plans = tmp_path / ".nanocursor/plans"
    plans.mkdir(parents=True)
    plan = plans / "current.md"
    target = tmp_path / "src/important.md"
    target.parent.mkdir()
    target.write_text("original")
    checker.plan_file_path = str(plan)
    if link_kind == "file":
        plan.symlink_to(target)
        requested = plan
    elif link_kind == "directory":
        plans.rmdir()
        plans.symlink_to(target.parent, target_is_directory=True)
        requested = plan
    else:
        plan.write_text("plan")
        alias = tmp_path / "alias.md"
        alias.symlink_to(plan)
        requested = alias
    assert decision(checker, requested).effect == "ask"
    assert target.read_text() == "original"
