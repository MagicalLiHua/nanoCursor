"""
Shared test fixtures.
"""

import os
import sys

import pytest

# Ensure repo root is on sys.path so `src` imports work.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Disable LLM classifier in tests to ensure deterministic keyword-based routing.
os.environ["LLM_CLASSIFIER_ENABLED"] = "false"


def _patch_file_tools_workspace(workspace_str, monkeypatch):
    """Patch both config and file_tools module-level WORKSPACE_DIR refs."""
    import src.infra.config
    monkeypatch.setattr(src.infra.config, "WORKSPACE_DIR", workspace_str, raising=False)

    import src.tools.file_tools
    monkeypatch.setattr(src.tools.file_tools, "WORKSPACE_DIR", workspace_str, raising=False)
    monkeypatch.setattr(src.tools.file_tools, "BACKUP_DIR", os.path.join(workspace_str, ".backups"), raising=False)
    _bk = os.path.join(workspace_str, ".backups")
    os.makedirs(_bk, exist_ok=True)


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Create a temporary workspace and redirect all file_tools paths to it."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _patch_file_tools_workspace(str(workspace), monkeypatch)
    return workspace
