"""Runtime boundary audit.

This test prevents new ad-hoc blocking process/network calls from quietly
appearing outside documented runtime boundaries.
"""

from __future__ import annotations

import re
from pathlib import Path


ALLOWLIST = {
    "src/runtime/command_runner.py",
    "src/runtime/git_runner.py",
    "src/runtime/go_runtime_client.py",
    "src/runtime/executor_client.py",
    "src/api/services/skill_github_import_service.py",
    "src/api/services/approval_service.py",
    "src/api/services/demo_run.py",
    "src/api/services/benchmark_service.py",
    "src/infra/cron.py",
    "src/api/services/intent_router.py",
    "src/team/team.py",
}

PATTERN = re.compile(r"subprocess\.run|time\.sleep|urllib\.request|urlopen|shell=True")


def test_blocking_runtime_calls_stay_in_documented_boundaries():
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in sorted((root / "src").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if not PATTERN.search(text):
            continue
        if relative in ALLOWLIST:
            continue
        offenders.append(relative)

    assert offenders == []
