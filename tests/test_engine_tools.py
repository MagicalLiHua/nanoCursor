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
