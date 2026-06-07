from src.agent import engine
from src.infra import config as config_module


def test_run_list_directory_is_cross_platform_and_does_not_create_nul(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    output = engine.run_list_directory(".")

    assert "src/" in output
    assert "README.md" in output
    assert not (tmp_path / "nul").exists()


def test_run_list_directory_hides_internal_and_generated_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(tmp_path))
    (tmp_path / ".nanocursor").mkdir()
    (tmp_path / ".backups").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    output = engine.run_list_directory(".")

    assert "src/" in output
    assert "README.md" in output
    assert ".nanocursor" not in output
    assert ".backups" not in output
    assert "__pycache__" not in output
    assert ".pytest_cache" not in output
